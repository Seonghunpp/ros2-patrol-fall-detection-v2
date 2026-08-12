#!/usr/bin/env python3
"""
병실 좌표 저장

로봇을 병실로 이동시킨 뒤(RViz의 2D Goal Pose 등)
이름 입력 -> 현재 로봇 위치(map 좌표)를 rooms.yaml에 저장

    ros2 run my_patrol waypoint_saver
"""
import os
import threading

import rclpy
from rclpy.node import Node
from tf2_ros import Buffer, TransformListener
import yaml


DEFAULT_ROOMS = os.path.expanduser(
    '~/turtlebot3_ws/ros2-patrol-fall-detection/src/my_patrol/config/rooms.yaml')


class WaypointSaver(Node):
    def __init__(self):
        super().__init__('waypoint_saver')
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    def current_pose(self):
        """map -> base_footprint 변환으로 현재 위치를 읽음. 없으면 None."""
        try:
            t = self.tf_buffer.lookup_transform(
                'map', 'base_footprint', rclpy.time.Time())
            return t.transform.translation, t.transform.rotation
        except Exception as e:  # noqa: BLE001
            self.get_logger().debug(f'TF 조회 실패: {e}')
            return None


def main():
    rclpy.init()
    node = WaypointSaver()
    # TF를 계속 받기 위해 백그라운드에서 spin
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()

    def capture(prompt, allow_skip=False):
        """안내 출력 후 Enter를 기다렸다가 현재 위치를 dict로 반환.
        q -> 'q'(취소), s -> 's'(skip, allow_skip일 때만)."""
        while rclpy.ok():
            ans = input(prompt).strip().lower()
            if ans == 'q':
                return 'q'
            if allow_skip and ans == 's':
                return 's'
            pose = node.current_pose()
            if pose is None:
                print('Unable to read current location. '
                      'Check Nav2/AMCL is active and 2D Pose Estimate is set.')
                continue
            trans, rot = pose
            return {
                'x': round(trans.x, 3), #좌표 
                'y': round(trans.y, 3),
                'z': round(rot.z, 4), #쿼터니언  
                'w': round(rot.w, 4),
            }

    rooms = {}
    print('=' * 50)
    print(' STEP 1/2 — wards (hall + inside)')
    print('  1) Enter the ward name (room1 ... room4)')
    print('  2) Move robot to the hallway in front of the door, press Enter -> hall saved')
    print('     (or press s to skip hall and save inside only)')
    print('  3) Move robot inside the ward, press Enter -> inside saved')
    print('  4) Type q at the name prompt to move on to STEP 2')
    print('')
    print(' STEP 2/2 — charging station, standby desk (one point each)')
    print('=' * 50)

    while rclpy.ok():
        name = input('ward name (q=finish): ').strip()
        if name.lower() == 'q':
            break
        if not name:
            continue

        hall = capture(
            f'[{name}] move to hallway in front of door, press Enter '
            f'(s=skip hall, q=cancel): ',
            allow_skip=True)
        if hall == 'q':
            continue
        if hall == 's':
            hall = None  # 복도점 생략 → inside만 저장

        inside = capture(f'[{name}] move inside the ward, press Enter (q=cancel): ')
        if inside == 'q':
            continue

        if hall is None:
            rooms[name] = {'inside': inside}
            print(f'  ✔ {name} saved — inside=({inside["x"]:.2f},{inside["y"]:.2f}) '
                  f'(hall skipped)')
        else:
            rooms[name] = {'hall': hall, 'inside': inside}
            print(f'  ✔ {name} saved — hall=({hall["x"]:.2f},{hall["y"]:.2f}) '
                  f'inside=({inside["x"]:.2f},{inside["y"]:.2f})')

    # 병실 외 단일 지점. hall/inside 구분이 없어서 한 점씩만 받는다
    print('\n' + '=' * 50)
    print(' STEP 2/2 — charging station, standby desk')
    print('=' * 50)

    def capture_single(label, prompt):
        point = capture(prompt, allow_skip=True)
        # capture()는 Ctrl+C 등으로 rclpy가 내려가면 None을 돌려준다
        if point is None or point in ('q', 's'):
            print(f'  - {label} skipped')
            return None
        print(f'  ✔ {label} saved — ({point["x"]:.2f},{point["y"]:.2f})')
        return point

    dock = capture_single(
        'dock',
        'move robot to CHARGING STATION, press Enter (s=skip, q=skip): ')
    standby = capture_single(
        'standby',
        'move robot to STANDBY DESK, press Enter (s=skip, q=skip): ')

    # 기존 파일을 먼저 읽어서 병합한다. 통째로 dump하면 이번에 입력하지 않은 항목이
    # 지워져서, 한 지점만 다시 찍으려다 나머지를 전부 날리게 된다
    data = {}
    if os.path.exists(DEFAULT_ROOMS):
        try:
            with open(DEFAULT_ROOMS) as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
            print(f'Warning: could not read existing file ({e}). Starting fresh.')
            data = {}

    if rooms:
        data.setdefault('rooms', {}).update(rooms)
    if dock:
        data['dock'] = dock
    if standby:
        data['standby'] = standby

    if data:
        # dock → standby → rooms 순으로 정렬해 저장 (읽기 좋게)
        ordered = {k: data[k] for k in ('dock', 'standby', 'rooms') if k in data}
        ordered.update({k: v for k, v in data.items() if k not in ordered})

        os.makedirs(os.path.dirname(DEFAULT_ROOMS), exist_ok=True)
        with open(DEFAULT_ROOMS, 'w') as f:
            yaml.dump(ordered, f, allow_unicode=True, sort_keys=False)

        saved = list(ordered.get('rooms', {}).keys())
        print(f'\nSaved → {DEFAULT_ROOMS}')
        print(f'  wards   : {saved if saved else "none"}')
        print(f'  dock    : {"yes" if "dock" in ordered else "no"}')
        print(f'  standby : {"yes" if "standby" in ordered else "no"}')
    else:
        print('\nNothing saved')

    rclpy.shutdown()


if __name__ == '__main__':
    main()
