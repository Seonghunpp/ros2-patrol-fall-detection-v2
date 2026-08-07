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

CREATE TABLE IF NOT EXISTS checklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    text VARCHAR(255) NOT NULL,
    done BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patrol_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    room_number VARCHAR(10) NOT NULL,
    patrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 보호자 연동 신청 (신청 -> 관리자 승인(매핑코드 발급) -> 신청자가 코드로 계정 생성)
-- applicant_name/phone/patient_name도 암호화해서 저장
CREATE TABLE IF NOT EXISTS guardian_applications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    applicant_name VARCHAR(255) NOT NULL,
    phone VARCHAR(255) NOT NULL,
    patient_name VARCHAR(255) NOT NULL,
    room_number VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',   -- pending / approved / rejected / registered
    mapping_code VARCHAR(20) UNIQUE,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

