CREATE DATABASE IF NOT EXISTS patrol_dashboard;
USE patrol_dashboard;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 간호사 명부 + 담당 병실 (병실 단위 배정)
-- 간호사는 로그인 계정이 아니라 '직원 명부' 데이터다. 로그인은 admin 계정(users)만 사용하고
-- 여러 간호사가 그 admin 계정을 공유한다. 그래서 users와 연결(user_id) 없이 독립 테이블로 둔다.
-- name/phone은 환자처럼 양방향 암호화해서 저장하므로 컬럼을 넉넉히(255) 잡는다.
-- assigned_room: 'all'(전체 101~104) 또는 '101'~'104'.
--   한 병실에 여러 간호사가 있으면 같은 assigned_room 값을 가진 행이 여러 개가 된다.
--   보호자 페이지는 자기 환자의 병실 번호로 이 표를 조회해 담당 간호사를 찾는다.
CREATE TABLE IF NOT EXISTS nurses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    employee_no VARCHAR(30),
    phone VARCHAR(255),
    assigned_room VARCHAR(20) NOT NULL DEFAULT 'all'
);

-- name/phone/guardian은 암호화해서 저장 (양방향 암호화라 컬럼을 넉넉하게 잡음)
CREATE TABLE IF NOT EXISTS patients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(255),
    disease VARCHAR(100),
    room_number VARCHAR(10),
    age INT,
    sex VARCHAR(5),
    risk_level VARCHAR(10),
    marker_id INT,
    user_id INT UNIQUE,
    guardian VARCHAR(255),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL,
    text VARCHAR(255) NOT NULL,
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS patrol_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    room_number VARCHAR(10) NOT NULL,
    patrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guardian_applications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    applicant_name VARCHAR(255) NOT NULL,
    phone VARCHAR(255) NOT NULL,
    patient_name VARCHAR(255) NOT NULL,
    room_number VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',   
    mapping_code VARCHAR(20) UNIQUE,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id INT UNIQUE,   
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 낙상 감지 기록. 감지되는 순간 서버가 자동으로 행을 만들고 캡처 이미지를 저장한다(capture_path).
-- patient_id/confirmed_by/memo/done은 관리자가 현장 확인 후 '낙상 이벤트' 화면에서 채운다(그 전까진 NULL/FALSE).
-- confirmed_at: 관리자가 낙상 환자를 확정(처리)한 시각. 보호자 화면 상태색(낙상→10분후 주의) 계산에 쓴다.
-- patient_name: 확정 당시 환자 이름을 암호화해 함께 남긴 값.
--   퇴원 처리는 patient_id를 NULL로 끊기 때문에(FK 제약), 이 값이 없으면 '누가 넘어졌는지'가
--   기록에서 영영 사라진다. 화면은 재원 중이면 patients의 이름을, 퇴원했으면 이 값을 보여준다.
--   통계 그래프는 patient_id 기준이라 퇴원한 환자의 낙상은 자동으로 빠진다.
CREATE TABLE IF NOT EXISTS fall_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    room_number VARCHAR(10) NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    capture_path VARCHAR(255),
    patient_id INT,
    patient_name VARCHAR(255),
    confirmed_by INT,
    memo VARCHAR(255),
    done BOOLEAN DEFAULT FALSE,
    confirmed_at TIMESTAMP NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (confirmed_by) REFERENCES users(id)
);


-- ============================================================
-- 기존 DB 업그레이드
-- ------------------------------------------------------------
-- 위의 CREATE TABLE은 IF NOT EXISTS라서, 이미 테이블이 있는 DB에는
-- 새 컬럼이 추가되지 않는다. 그래서 아래를 따로 둔다.
-- 여러 번 실행해도 안전하다(이미 반영돼 있으면 아무 일도 하지 않는다).
-- ============================================================

-- fall_log.patient_name 추가 (2026-08-14)
SET @add_patient_name := (
    SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'fall_log'
      AND COLUMN_NAME  = 'patient_name'
);
SET @stmt := IF(@add_patient_name,
    'ALTER TABLE fall_log ADD COLUMN patient_name VARCHAR(255) NULL AFTER patient_id',
    'DO 0');
PREPARE s FROM @stmt; EXECUTE s; DEALLOCATE PREPARE s;

-- 이미 확정돼 있던 기록에 현재 환자 이름을 채워 넣는다.
-- 안 채우면 그 환자가 퇴원하는 순간 이름이 사라진다.
UPDATE fall_log f
  JOIN patients p ON p.id = f.patient_id
   SET f.patient_name = p.name
 WHERE f.patient_id IS NOT NULL AND f.patient_name IS NULL;

-- checklist 테이블 제거 (2026-08-14)
DROP TABLE IF EXISTS checklist;

