import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

try:
    import serial
except ImportError:
    serial = None


class BuzzerBridge(Node):
    """터틀봇에 붙은 아두이노(부저)와 ROS2 사이를 잇는 노드.

    켜기: fall_detection_node_v2가 내는 /fall_confirmed(Bool)를 직접 구독한다.
    끄기: 대시보드가 "/buzzer_off"로 알려준다 — 사람이 웹에서 낙상 팝업을
          확인(닫기)했다는 뜻이라, 서버 쪽에서만 알 수 있는 신호이기 때문이다.
    """

    def __init__(self):
        super().__init__("buzzer_bridge")

        self.declare_parameter("serial_port", "/dev/ttyACM0")
        self.declare_parameter("baud_rate", 9600)
        port = self.get_parameter("serial_port").get_parameter_value().string_value
        baud = self.get_parameter("baud_rate").get_parameter_value().integer_value

        self.ser = None
        if serial is None:
            self.get_logger().error("pyserial이 설치되어 있지 않습니다. pip install pyserial")
        else:
            try:
                self.ser = serial.Serial(port, baud, timeout=1)
            except serial.SerialException as e:
                self.get_logger().error(f"아두이노 시리얼 포트 연결 실패({port}): {e}")

        self.create_subscription(Bool, "/fall_confirmed", self.fall_confirmed_callback, 10)
        self.create_subscription(String, "/buzzer_off", self.buzzer_off_callback, 10)

        self.get_logger().info("buzzer_bridge started")

    def _write(self, cmd: bytes):
        if self.ser is None:
            return
        try:
            self.ser.write(cmd)
        except serial.SerialException as e:
            self.get_logger().error(f"아두이노로 시리얼 쓰기 실패: {e}")

    def fall_confirmed_callback(self, msg):
        if msg.data:
            self._write(b"1")   # 부저 ON

    def buzzer_off_callback(self, msg):
        self._write(b"0")       # 부저 OFF


def main():
    rclpy.init()
    node = BuzzerBridge()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
