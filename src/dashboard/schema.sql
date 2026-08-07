CREATE DATABASE IF NOT EXISTS patrol_dashboard;
USE patrol_dashboard;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
CREATE TABLE IF NOT EXISTS fall_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    room_number VARCHAR(10) NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    capture_path VARCHAR(255),
    patient_id INT,
    confirmed_by INT,
    memo VARCHAR(255),
    done BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (confirmed_by) REFERENCES users(id)
);

