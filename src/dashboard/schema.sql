CREATE DATABASE IF NOT EXISTS patrol_dashboard;
USE patrol_dashboard;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    phone VARCHAR(20),
    disease VARCHAR(100),
    room_number VARCHAR(10),
    age INT,
    marker_id INT,
    user_id INT UNIQUE,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

