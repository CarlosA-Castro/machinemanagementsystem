show databases;
use maquinasmedellin;
CREATE TABLE Users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    password VARCHAR(255) NOT NULL,
    role ENUM('admin', 'cajero',  'admin_restaurante') NOT NULL,
    createdBy INT,
    createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);
CREATE TABLE Location (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    address VARCHAR(200),
    city VARCHAR(100),
    status ENUM('activo', 'inactivo') DEFAULT 'activo'
);
CREATE TABLE Machine (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(100),
    locationId INT,
    owner VARCHAR(100),
    dailyFailedTurns INT DEFAULT 0,
    dateLastQRUsed DATETIME,
    errorNote TEXT,
    FOREIGN KEY (locationId) REFERENCES Location(id)
);
CREATE TABLE TurnPackage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    turns INT NOT NULL,
    price INT NOT NULL,
    isActive BOOLEAN DEFAULT TRUE,
    createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE QRCode (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(100) NOT NULL UNIQUE,
    turnPackageId INT NOT NULL,
    remainingTurns INT NOT NULL,
    isUsed BOOLEAN DEFAULT FALSE,
    isActive BOOLEAN DEFAULT TRUE,
    createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (turnPackageId) REFERENCES TurnPackage(id)
);
CREATE TABLE TurnUsage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    qrCodeId INT NOT NULL,
    machineId INT NOT NULL,
    usedAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (qrCodeId) REFERENCES QRCode(id),
    FOREIGN KEY (machineId) REFERENCES Machine(id)
);
CREATE TABLE ErrorReport (
    id INT AUTO_INCREMENT PRIMARY KEY,
    machineId INT NOT NULL,
    userId INT NOT NULL,
    description TEXT NOT NULL,
    reportedAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    isResolved BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (machineId) REFERENCES Machine(id),
    FOREIGN KEY (userId) REFERENCES User(id)
);
CREATE TABLE SessionLog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    userId INT NOT NULL,
    loginTime DATETIME DEFAULT CURRENT_TIMESTAMP,
    logoutTime DATETIME,
    FOREIGN KEY (userId) REFERENCES User(id)
);
CREATE TABLE Confirmation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fault_report_id INT NOT NULL,
    admin_id INT NOT NULL,
    confirmation_status ENUM('confirmada', 'rechazada', 'resuelta') NOT NULL,
    comments TEXT,
    confirmation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (fault_report_id) REFERENCES ErrorReport(id),
    FOREIGN KEY (admin_id) REFERENCES User(id)
);
INSERT INTO location (id, name, address, city) VALUES
(1, 'El mekatiadero', 'Diagonal 52, Ingreso Poblado Niquia #15a-351', 'Niquia');
INSERT INTO Users (id, name, role, createdBy, password) VALUES
(1, 'Carlos Andrés Castro', 'admin', NULL, '123456789'),
(2, 'Andrés Felipe Gomez', 'admin', 1, '987654321'),
(3, 'Carolina', 'cajero', 1, '172839142'),
(4, 'Camila', 'admin_restaurante', 1, '392817392');
ALTER TABLE machine
ADD COLUMN status ENUM('activa', 'inactiva', 'mantenimiento') NOT NULL DEFAULT 'activa';
ALTER TABLE machine
ADD COLUMN location_id INT NOT NULL;
INSERT INTO machine (id, name, type, status, location_id) VALUES
(1, 'Simulador connection', 'simulador', 'activa', 1),
(2, 'Simulador Cruisin 1', 'simulador', 'activa', 1),
(3, 'Simulador Cruisin 2', 'simulador', 'activa', 1),
(4, 'Peluches 1', 'peluchera', 'activa', 1),
(5, 'Peluches 2', 'peluchera', 'activa', 1),
(6, 'Basketball', 'arcade', 'activa', 1),
(7, 'Pelea', 'arcade', 'activa', 1),
(8, 'Disco hockey', 'arcade', 'activa', 1),
(9, 'Sillas masajes', 'simulador', 'activa', 1),
(10, 'Mcqueen', 'arcade', 'activa', 1),
(11, 'Caballito', 'arcade', 'activa', 1),
(12, 'Trencito', 'arcade', 'activa', 1);
INSERT INTO TurnPackage (name, turns, price) VALUES
('Paquete P1', 4, 10000),
('Paquete P2', 6, 13000),
('Paquete P3', 8, 15000),
('Paquete P4', 10, 18000),
('Paquete P5', 12, 20000),
('Paquete P6', 14, 22000),
('Paquete P7', 16, 24000),
('Paquete P8', 18, 26000),
('Paquete P9', 20, 28000),
('Paquete P10', 22, 30000);























