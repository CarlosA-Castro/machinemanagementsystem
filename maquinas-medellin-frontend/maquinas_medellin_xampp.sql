-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Servidor: 127.0.0.1
-- Tiempo de generación: 04-01-2026 a las 01:07:02
-- Versión del servidor: 10.4.32-MariaDB
-- Versión de PHP: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Base de datos: `maquinasmedellin`
--
CREATE DATABASE IF NOT EXISTS `maquinasmedellin` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `maquinasmedellin`;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `confirmation`
--

DROP TABLE IF EXISTS `confirmation`;
CREATE TABLE IF NOT EXISTS `confirmation` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fault_report_id` int(11) NOT NULL,
  `admin_id` int(11) NOT NULL,
  `confirmation_status` enum('confirmada','rechazada','resuelta') NOT NULL,
  `comments` text DEFAULT NULL,
  `confirmation_date` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `fault_report_id` (`fault_report_id`),
  KEY `admin_id` (`admin_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `confirmation_logs`
--

DROP TABLE IF EXISTS `confirmation_logs`;
CREATE TABLE IF NOT EXISTS `confirmation_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fault_report_id` int(11) NOT NULL,
  `admin_id` int(11) NOT NULL,
  `confirmation_status` enum('pendiente','resuelta') DEFAULT 'pendiente',
  `comments` text DEFAULT NULL,
  `confirmed_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_fault_report` (`fault_report_id`),
  KEY `idx_admin` (`admin_id`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `confirmation_logs`
--

INSERT INTO `confirmation_logs` (`id`, `fault_report_id`, `admin_id`, `confirmation_status`, `comments`, `confirmed_at`) VALUES
(1, 5, 1, 'resuelta', 'Prueba manual', '2026-01-02 15:19:08'),
(2, 5, 1, 'resuelta', 'test desde API', '2026-01-02 15:25:26'),
(3, 5, 1, 'resuelta', '', '2026-01-02 15:29:23'),
(4, 6, 1, 'resuelta', '', '2026-01-02 15:29:55'),
(5, 3, 1, 'resuelta', '', '2026-01-02 15:29:59'),
(6, 3, 1, 'resuelta', '', '2026-01-02 15:30:02'),
(7, 2, 1, 'resuelta', '', '2026-01-02 15:30:03'),
(8, 1, 1, 'resuelta', '', '2026-01-02 15:30:04'),
(9, 4, 1, 'resuelta', '', '2026-01-02 15:30:15');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `contadordiario`
--

DROP TABLE IF EXISTS `contadordiario`;
CREATE TABLE IF NOT EXISTS `contadordiario` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fecha` date NOT NULL,
  `qr_vendidos` int(11) DEFAULT 0,
  `valor_ventas` decimal(10,2) DEFAULT 0.00,
  `qr_escaneados` int(11) DEFAULT 0,
  `turnos_utilizados` int(11) DEFAULT 0,
  `fallas_reportadas` int(11) DEFAULT 0,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `fecha` (`fecha`),
  KEY `idx_fecha` (`fecha`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `contadordiario`
--

INSERT INTO `contadordiario` (`id`, `fecha`, `qr_vendidos`, `valor_ventas`, `qr_escaneados`, `turnos_utilizados`, `fallas_reportadas`, `created_at`, `updated_at`) VALUES
(1, '2025-12-26', 5, 113000.00, 8, 0, 0, '2025-12-26 16:39:06', '2025-12-26 16:39:06'),
(2, '2025-12-29', 2, 44000.00, 2, 0, 0, '2025-12-29 14:30:27', '2025-12-29 14:36:47'),
(4, '2025-12-30', 1, 30000.00, 1, 0, 0, '2025-12-30 13:49:49', '2025-12-30 13:49:49');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `errorreport`
--

DROP TABLE IF EXISTS `errorreport`;
CREATE TABLE IF NOT EXISTS `errorreport` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `machineId` int(11) NOT NULL,
  `userId` int(11) NOT NULL,
  `description` text NOT NULL,
  `reportedAt` datetime DEFAULT current_timestamp(),
  `isResolved` tinyint(1) DEFAULT 0,
  `problem_type` varchar(50) DEFAULT 'mantenimiento',
  `resolved_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `machineId` (`machineId`),
  KEY `userId` (`userId`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `errorreport`
--

INSERT INTO `errorreport` (`id`, `machineId`, `userId`, `description`, `reportedAt`, `isResolved`, `problem_type`, `resolved_at`) VALUES
(1, 8, 1, 'malo', '2025-12-29 13:42:40', 1, 'mantenimiento', '2026-01-02 15:30:04'),
(2, 8, 1, 'dsfsd', '2025-12-29 13:42:57', 1, 'mantenimiento', '2026-01-02 15:30:03'),
(3, 8, 1, 'asdas', '2025-12-29 13:45:13', 1, 'mantenimiento', '2026-01-02 15:30:01'),
(4, 4, 1, 'explotó', '2025-12-29 13:46:05', 1, 'mantenimiento', '2026-01-02 15:30:15'),
(5, 6, 1, 'Los balones no bajan', '2025-12-29 14:37:34', 1, 'mantenimiento', '2026-01-02 15:29:23'),
(6, 8, 1, 'explotó', '2025-12-30 13:46:11', 1, 'mantenimiento', '2026-01-02 15:29:55');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `globalcounter`
--

DROP TABLE IF EXISTS `globalcounter`;
CREATE TABLE IF NOT EXISTS `globalcounter` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `counter_type` varchar(50) NOT NULL,
  `counter_value` int(11) NOT NULL DEFAULT 0,
  `description` varchar(255) DEFAULT NULL,
  `last_updated` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `counter_type` (`counter_type`),
  KEY `idx_counter_type` (`counter_type`)
) ENGINE=InnoDB AUTO_INCREMENT=100 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `globalcounter`
--

INSERT INTO `globalcounter` (`id`, `counter_type`, `counter_value`, `description`, `last_updated`) VALUES
(1, 'QR_CODE', 5, 'Contador para códigos QR (formato QR0001, QR0002, etc.)', '2026-01-02 15:46:21'),
(2, 'VENTA_DIARIA', 0, 'Contador de ventas diarias', '2025-12-26 15:34:48'),
(3, 'TURNOS_USADOS', 0, 'Contador de turnos utilizados', '2025-12-26 15:34:48');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `inversiones`
--

DROP TABLE IF EXISTS `inversiones`;
CREATE TABLE IF NOT EXISTS `inversiones` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `socio_id` int(11) NOT NULL,
  `maquina_id` int(11) NOT NULL,
  `porcentaje_inversion` decimal(5,2) NOT NULL,
  `fecha_inicio` date NOT NULL,
  `fecha_fin` date DEFAULT NULL,
  `monto_inicial` decimal(10,2) DEFAULT 0.00,
  `estado` enum('activa','finalizada','suspendida') DEFAULT 'activa',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_socio_maquina` (`socio_id`,`maquina_id`),
  KEY `maquina_id` (`maquina_id`),
  KEY `idx_estado_inversion` (`estado`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `liquidaciones`
--

DROP TABLE IF EXISTS `liquidaciones`;
CREATE TABLE IF NOT EXISTS `liquidaciones` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fecha` date NOT NULL,
  `maquina_id` int(11) NOT NULL,
  `turnos_retirados` int(11) NOT NULL,
  `valor_por_turno` decimal(10,2) NOT NULL,
  `costos_operativos` decimal(10,2) DEFAULT 0.00,
  `porcentaje_restaurante` decimal(5,2) DEFAULT 35.00,
  `observaciones` text DEFAULT NULL,
  `usuario_id` int(11) NOT NULL,
  `creado_el` timestamp NOT NULL DEFAULT current_timestamp(),
  `actualizado_el` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `usuario_id` (`usuario_id`),
  KEY `idx_fecha` (`fecha`),
  KEY `idx_maquina` (`maquina_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `location`
--

DROP TABLE IF EXISTS `location`;
CREATE TABLE IF NOT EXISTS `location` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `address` varchar(200) DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `status` enum('activo','inactivo') DEFAULT 'activo',
  `telefono` varchar(20) DEFAULT NULL,
  `horario` varchar(100) DEFAULT NULL,
  `notas` text DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `location`
--

INSERT INTO `location` (`id`, `name`, `address`, `city`, `status`, `telefono`, `horario`, `notas`) VALUES
(1, 'El mekatiadero', 'Diagonal 52, Ingreso Poblado Niquia #15a-351', 'Niquia', 'activo', NULL, NULL, NULL);

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `machine`
--

DROP TABLE IF EXISTS `machine`;
CREATE TABLE IF NOT EXISTS `machine` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `type` varchar(100) DEFAULT NULL,
  `location_id` int(11) NOT NULL,
  `owner` varchar(100) DEFAULT NULL,
  `dailyFailedTurns` int(11) DEFAULT 0,
  `dateLastQRUsed` datetime DEFAULT NULL,
  `errorNote` text DEFAULT NULL,
  `status` enum('activa','inactiva','mantenimiento') NOT NULL DEFAULT 'activa',
  `valor_por_turno` decimal(10,2) DEFAULT 3000.00,
  PRIMARY KEY (`id`),
  KEY `location_id` (`location_id`)
) ENGINE=InnoDB AUTO_INCREMENT=13 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `machine`
--

INSERT INTO `machine` (`id`, `name`, `type`, `location_id`, `owner`, `dailyFailedTurns`, `dateLastQRUsed`, `errorNote`, `status`, `valor_por_turno`) VALUES
(1, 'Simulador connection', 'simulador', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(2, 'Simulador Cruisin 1', 'simulador', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(3, 'Simulador Cruisin 2', 'simulador', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(4, 'Peluches 1', 'peluchera', 1, NULL, 1, NULL, 'explotó', 'activa', 3000.00),
(5, 'Peluches 2', 'peluchera', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(6, 'Basketball', 'arcade', 1, NULL, 1, NULL, 'Los balones no bajan', 'activa', 3000.00),
(7, 'Pelea', 'arcade', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(8, 'Disco hockey', 'arcade', 1, NULL, 4, NULL, 'explotó', 'activa', 3000.00),
(9, 'Sillas masajes', 'simulador', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(10, 'Mcqueen', 'arcade', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(11, 'Caballito', 'arcade', 1, NULL, 0, NULL, NULL, 'activa', 3000.00),
(12, 'Trencito', 'arcade', 1, NULL, 0, NULL, NULL, 'activa', 3000.00);

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `machinefailures`
--

DROP TABLE IF EXISTS `machinefailures`;
CREATE TABLE IF NOT EXISTS `machinefailures` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `qr_code_id` int(11) NOT NULL,
  `machine_id` int(11) NOT NULL,
  `machine_name` varchar(100) DEFAULT NULL,
  `turnos_devueltos` int(11) NOT NULL,
  `reported_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `qr_code_id` (`qr_code_id`),
  KEY `machine_id` (`machine_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `maquinaporcentajerestaurante`
--

DROP TABLE IF EXISTS `maquinaporcentajerestaurante`;
CREATE TABLE IF NOT EXISTS `maquinaporcentajerestaurante` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `maquina_id` int(11) NOT NULL,
  `porcentaje_restaurante` decimal(5,2) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_maquina_porcentaje` (`maquina_id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `maquinaporcentajerestaurante`
--

INSERT INTO `maquinaporcentajerestaurante` (`id`, `maquina_id`, `porcentaje_restaurante`) VALUES
(1, 4, 30.00),
(2, 5, 30.00);

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `maquinapropietario`
--

DROP TABLE IF EXISTS `maquinapropietario`;
CREATE TABLE IF NOT EXISTS `maquinapropietario` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `maquina_id` int(11) NOT NULL,
  `propietario_id` int(11) NOT NULL,
  `porcentaje_propiedad` decimal(5,2) NOT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_maquina_propietario` (`maquina_id`,`propietario_id`),
  KEY `propietario_id` (`propietario_id`)
) ENGINE=InnoDB AUTO_INCREMENT=19 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `maquinapropietario`
--

INSERT INTO `maquinapropietario` (`id`, `maquina_id`, `propietario_id`, `porcentaje_propiedad`, `created_at`) VALUES
(1, 9, 1, 50.00, '2025-12-26 01:46:33'),
(2, 9, 3, 50.00, '2025-12-26 01:46:33'),
(3, 6, 1, 50.00, '2025-12-26 01:46:33'),
(4, 6, 3, 50.00, '2025-12-26 01:46:33'),
(5, 8, 1, 50.00, '2025-12-26 01:46:33'),
(6, 8, 2, 50.00, '2025-12-26 01:46:33'),
(7, 4, 1, 50.00, '2025-12-26 01:46:33'),
(8, 4, 2, 50.00, '2025-12-26 01:46:33'),
(9, 5, 1, 50.00, '2025-12-26 01:46:33'),
(10, 5, 2, 50.00, '2025-12-26 01:46:33'),
(11, 7, 1, 50.00, '2025-12-26 01:46:33'),
(12, 7, 2, 50.00, '2025-12-26 01:46:33'),
(13, 1, 2, 100.00, '2025-12-26 01:46:33'),
(14, 2, 2, 100.00, '2025-12-26 01:46:33'),
(15, 3, 2, 100.00, '2025-12-26 01:46:33'),
(16, 10, 1, 100.00, '2025-12-26 01:46:33'),
(17, 11, 1, 100.00, '2025-12-26 01:46:33'),
(18, 12, 1, 100.00, '2025-12-26 01:46:33');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `pagoscuotas`
--

DROP TABLE IF EXISTS `pagoscuotas`;
CREATE TABLE IF NOT EXISTS `pagoscuotas` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `socio_id` int(11) NOT NULL,
  `anio` year(4) NOT NULL,
  `monto` decimal(10,2) NOT NULL,
  `fecha_pago` date DEFAULT NULL,
  `metodo_pago` enum('efectivo','transferencia','tarjeta','cheque') DEFAULT 'efectivo',
  `comprobante` varchar(255) DEFAULT NULL,
  `estado` enum('pendiente','pagado','vencido','anulado') DEFAULT 'pendiente',
  `notas` text DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_socio_anio` (`socio_id`,`anio`),
  KEY `idx_estado_pago` (`estado`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `propietarios`
--

DROP TABLE IF EXISTS `propietarios`;
CREATE TABLE IF NOT EXISTS `propietarios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(100) NOT NULL,
  `telefono` varchar(20) DEFAULT NULL,
  `email` varchar(100) DEFAULT NULL,
  `porcentaje_restaurante` decimal(5,2) DEFAULT 0.00,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `propietarios`
--

INSERT INTO `propietarios` (`id`, `nombre`, `telefono`, `email`, `porcentaje_restaurante`, `created_at`) VALUES
(1, 'Edison', NULL, NULL, 35.00, '2025-12-26 01:46:33'),
(2, 'Andrés', NULL, NULL, 35.00, '2025-12-26 01:46:33'),
(3, 'Beliza', NULL, NULL, 35.00, '2025-12-26 01:46:33');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `qrcode`
--

DROP TABLE IF EXISTS `qrcode`;
CREATE TABLE IF NOT EXISTS `qrcode` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `code` varchar(100) NOT NULL,
  `turnPackageId` int(11) DEFAULT 1,
  `remainingTurns` int(11) NOT NULL,
  `isUsed` tinyint(1) DEFAULT 0,
  `isActive` tinyint(1) DEFAULT 1,
  `createdAt` datetime DEFAULT current_timestamp(),
  `qr_name` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`),
  KEY `fk_qrcode_turnpackage` (`turnPackageId`)
) ENGINE=InnoDB AUTO_INCREMENT=22 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `qrcode`
--

INSERT INTO `qrcode` (`id`, `code`, `turnPackageId`, `remainingTurns`, `isUsed`, `isActive`, `createdAt`, `qr_name`) VALUES
(1, 'QR0006', 9, 20, 0, 1, '2025-12-26 14:05:30', 'error'),
(2, 'QR0007', 5, 12, 0, 1, '2025-12-26 14:15:16', 'jhkjh'),
(3, 'QR0008', 1, 4, 0, 1, '2025-12-26 14:47:25', 'klk'),
(4, 'QR009', 2, 6, 0, 1, '2025-12-26 15:28:42', 'example'),
(5, 'QR010', 8, 18, 0, 1, '2025-12-26 15:30:02', '435'),
(6, 'QR011', 8, 18, 0, 1, '2025-12-26 15:35:23', 'xxx'),
(7, 'QR012', 5, 12, 0, 1, '2025-12-26 16:03:49', 'si'),
(8, 'QR013', 9, 20, 0, 1, '2025-12-26 16:39:06', 'no'),
(9, 'QR014', 2, 6, 0, 1, '2025-12-29 14:30:27', 'video demostrativo'),
(10, 'QR015', 4, 10, 0, 1, '2025-12-29 14:36:47', 'Video demostrativo numero 2'),
(11, 'QR016', 10, 22, 0, 1, '2025-12-30 13:49:49', 'qr de la prima'),
(12, 'QR0001', 1, 0, 0, 1, '2025-12-30 16:01:50', ''),
(13, 'QR0002', 1, 0, 0, 1, '2025-12-30 16:01:57', 'ids nuevos'),
(14, 'QR0003', 1, 0, 0, 1, '2025-12-30 16:02:29', 'aaa'),
(15, 'QR0004', 1, 0, 0, 1, '2026-01-02 15:34:03', 'mi casa'),
(16, 'QR0005', 1, 0, 0, 1, '2026-01-02 15:46:21', 'hola');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `qrhistory`
--

DROP TABLE IF EXISTS `qrhistory`;
CREATE TABLE IF NOT EXISTS `qrhistory` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `qr_code` varchar(255) NOT NULL,
  `user_id` int(11) DEFAULT NULL,
  `user_name` varchar(100) DEFAULT NULL,
  `local` varchar(100) NOT NULL,
  `fecha_hora` datetime DEFAULT current_timestamp(),
  `qr_name` varchar(255) DEFAULT NULL,
  `es_venta_real` tinyint(1) DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_qr_code` (`qr_code`),
  KEY `idx_fecha_hora` (`fecha_hora`),
  KEY `idx_es_venta_real` (`es_venta_real`)
) ENGINE=InnoDB AUTO_INCREMENT=22 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `qrhistory`
--

INSERT INTO `qrhistory` (`id`, `qr_code`, `user_id`, `user_name`, `local`, `fecha_hora`, `qr_name`, `es_venta_real`) VALUES
(1, 'QR006', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 14:05:30', 'error', 0),
(2, 'QR007', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 14:15:16', 'jhkjh', 0),
(3, 'QR008', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 14:47:25', 'klk', 0),
(4, 'QR009', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 15:28:42', 'example', 1),
(5, 'QR010', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 15:30:02', '435', 1),
(6, 'QR011', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 15:35:23', 'xxx', 1),
(7, 'QR012', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 16:03:49', 'si', 1),
(8, 'QR013', 2, 'Andrés Felipe Gomez', 'El Mekatiadero', '2025-12-26 16:39:06', 'no', 1),
(9, 'QR014', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-29 14:30:27', 'video demostrativo', 1),
(10, 'QR014', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-29 14:31:43', 'video demostrativo', 1),
(11, 'QR015', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-29 14:36:47', 'Video demostrativo numero 2', 1),
(12, 'QR014', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-29 14:37:07', 'video demostrativo', 1),
(13, 'QR016', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-30 13:49:49', 'qr de la prima', 1),
(14, 'QR016', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-30 13:51:51', 'qr de la prima', 1),
(15, 'QR0001', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-30 16:01:50', '', 0),
(16, 'QR0002', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-30 16:01:57', 'ids nuevos', 0),
(17, 'QR0003', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2025-12-30 16:02:29', 'aaa', 0),
(18, 'QR0004', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2026-01-02 15:34:03', 'mi casa', 0),
(19, 'QR0004', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2026-01-02 15:34:30', 'mi casa', 0),
(20, 'QR0005', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2026-01-02 15:46:21', 'hola', 0),
(21, 'QR0005', 1, 'Carlos Andrés Castro', 'El Mekatiadero', '2026-01-02 15:46:48', 'hola', 0);

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `rendimientos`
--

DROP TABLE IF EXISTS `rendimientos`;
CREATE TABLE IF NOT EXISTS `rendimientos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `socio_id` int(11) NOT NULL,
  `periodo` date NOT NULL,
  `maquina_id` int(11) DEFAULT NULL,
  `turnos_totales` int(11) DEFAULT 0,
  `ingresos_brutos` decimal(10,2) DEFAULT 0.00,
  `costos_operativos` decimal(10,2) DEFAULT 0.00,
  `porcentaje_restaurante` decimal(5,2) DEFAULT 35.00,
  `ganancia_neta` decimal(10,2) DEFAULT 0.00,
  `porcentaje_socio` decimal(5,2) DEFAULT 0.00,
  `rendimiento_socio` decimal(10,2) DEFAULT 0.00,
  `liquidado` tinyint(1) DEFAULT 0,
  `fecha_liquidacion` datetime DEFAULT NULL,
  `comentarios` text DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `maquina_id` (`maquina_id`),
  KEY `idx_periodo` (`periodo`),
  KEY `idx_socio_periodo` (`socio_id`,`periodo`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `resolucionreportes`
--

DROP TABLE IF EXISTS `resolucionreportes`;
CREATE TABLE IF NOT EXISTS `resolucionreportes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `error_report_id` int(11) NOT NULL,
  `admin_id` int(11) NOT NULL,
  `comentarios` text DEFAULT NULL,
  `fecha_resolucion` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_error_report` (`error_report_id`),
  KEY `idx_admin` (`admin_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `sessionlog`
--

DROP TABLE IF EXISTS `sessionlog`;
CREATE TABLE IF NOT EXISTS `sessionlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `userId` int(11) NOT NULL,
  `loginTime` datetime DEFAULT current_timestamp(),
  `logoutTime` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `userId` (`userId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `socios`
--

DROP TABLE IF EXISTS `socios`;
CREATE TABLE IF NOT EXISTS `socios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `codigo_socio` varchar(20) NOT NULL,
  `nombre` varchar(100) NOT NULL,
  `documento` varchar(20) NOT NULL,
  `tipo_documento` enum('CC','CE','NIT','PASAPORTE') DEFAULT 'CC',
  `telefono` varchar(20) DEFAULT NULL,
  `email` varchar(100) DEFAULT NULL,
  `direccion` text DEFAULT NULL,
  `fecha_inscripcion` date NOT NULL,
  `fecha_vencimiento` date NOT NULL,
  `cuota_anual` decimal(10,2) DEFAULT 0.00,
  `estado` enum('activo','inactivo','pendiente_pago','suspendido') DEFAULT 'activo',
  `notas` text DEFAULT NULL,
  `porcentaje_global` decimal(5,2) DEFAULT 0.00,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `codigo_socio` (`codigo_socio`),
  UNIQUE KEY `documento` (`documento`),
  KEY `idx_estado` (`estado`),
  KEY `idx_fecha_vencimiento` (`fecha_vencimiento`),
  KEY `idx_codigo_socio` (`codigo_socio`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `system_messages`
--

DROP TABLE IF EXISTS `system_messages`;
CREATE TABLE IF NOT EXISTS `system_messages` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `message_code` varchar(50) NOT NULL,
  `message_type` enum('error','success','warning','info') NOT NULL,
  `message_text` text NOT NULL,
  `language_code` varchar(10) DEFAULT 'es',
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `message_code` (`message_code`),
  KEY `idx_message_code` (`message_code`),
  KEY `idx_message_type` (`message_type`)
) ENGINE=InnoDB AUTO_INCREMENT=50 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `system_messages`
--

INSERT INTO `system_messages` (`id`, `message_code`, `message_type`, `message_text`, `language_code`, `created_at`, `updated_at`) VALUES
(1, 'E001', 'error', 'Error interno del servidor', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(2, 'E002', 'error', 'Recurso no encontrado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(3, 'E003', 'error', 'No autorizado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(4, 'E004', 'error', 'Acceso prohibido', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(5, 'E005', 'error', 'Parámetros inválidos', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(6, 'E006', 'error', 'Error de conexión a la base de datos', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(7, 'E007', 'error', 'Operación no permitida', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(8, 'A001', 'error', 'Credenciales inválidas', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(9, 'A002', 'error', 'Sesión expirada', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(10, 'A003', 'error', 'Token inválido', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(11, 'A004', 'error', 'Usuario no autenticado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(12, 'M001', 'error', 'Máquina no encontrada', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(13, 'M002', 'error', 'QR inválido o expirado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(14, 'M003', 'error', 'Máquina no disponible', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(15, 'M004', 'error', 'Tiempo de juego agotado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(16, 'M005', 'error', 'Créditos insuficientes', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(17, 'M006', 'error', 'Máquina en mantenimiento', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(18, 'M007', 'error', 'Reporte de falla no encontrado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(19, 'Q001', 'error', 'Código QR no encontrado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(20, 'Q002', 'error', 'QR ya tiene paquete asignado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(21, 'Q003', 'error', 'No hay turnos disponibles', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(22, 'Q004', 'error', 'Paquete no encontrado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(23, 'Q005', 'error', 'Faltan datos requeridos', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(24, 'U001', 'error', 'Usuario no encontrado', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(25, 'U002', 'error', 'Usuario ya existe', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(26, 'U003', 'error', 'Contraseña demasiado corta', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(27, 'U004', 'error', 'Rol inválido', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(28, 'U005', 'error', 'No puedes eliminar tu propio usuario', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(29, 'S001', 'success', 'Operación exitosa', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(30, 'S002', 'success', 'Registro creado correctamente', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(31, 'S003', 'success', 'Actualización completada', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(32, 'S004', 'success', 'Eliminación exitosa', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(33, 'S005', 'success', 'Sesión iniciada correctamente', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(34, 'S006', 'success', 'QR guardado en historial', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(35, 'S007', 'success', 'Venta registrada correctamente', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(36, 'S008', 'success', 'Máquina reactivada', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(37, 'S009', 'success', 'Reporte resuelto', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(38, 'S010', 'success', 'Turno utilizado correctamente', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(39, 'I001', 'info', 'Procesando solicitud', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(40, 'I002', 'info', 'Redirigiendo...', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(41, 'I003', 'info', 'Cargando datos', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(42, 'I004', 'info', 'Generando reporte', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(43, 'W001', 'warning', 'Datos parcialmente guardados', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(44, 'W002', 'warning', 'Tiempo límite aproximándose', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(45, 'W003', 'warning', 'Conexión inestable', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(46, 'W004', 'warning', 'Máquina tiene usos registrados', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(47, 'W005', 'warning', 'Local tiene máquinas asignadas', 'es', '2025-12-26 01:46:33', '2025-12-26 01:46:33'),
(48, 'E020', 'error', 'Error de prueba', 'es', '2025-12-26 17:47:31', '2025-12-26 17:47:31'),
(49, 'E030', 'success', 'Suerte', 'es', '2025-12-26 18:04:30', '2025-12-26 18:04:30');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `turnpackage`
--

DROP TABLE IF EXISTS `turnpackage`;
CREATE TABLE IF NOT EXISTS `turnpackage` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `turns` int(11) NOT NULL,
  `price` int(11) NOT NULL,
  `isActive` tinyint(1) DEFAULT 1,
  `createdAt` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `turnpackage`
--

INSERT INTO `turnpackage` (`id`, `name`, `turns`, `price`, `isActive`, `createdAt`) VALUES
(1, 'Paquete P1', 4, 10000, 1, '2025-12-26 01:46:33'),
(2, 'Paquete P2', 6, 13000, 1, '2025-12-26 01:46:33'),
(3, 'Paquete P3', 8, 15000, 1, '2025-12-26 01:46:33'),
(4, 'Paquete P4', 10, 18000, 1, '2025-12-26 01:46:33'),
(5, 'Paquete P5', 12, 20000, 1, '2025-12-26 01:46:33'),
(6, 'Paquete P6', 14, 22000, 1, '2025-12-26 01:46:33'),
(7, 'Paquete P7', 16, 24000, 1, '2025-12-26 01:46:33'),
(8, 'Paquete P8', 18, 26000, 1, '2025-12-26 01:46:33'),
(9, 'Paquete P9', 20, 28000, 1, '2025-12-26 01:46:33'),
(10, 'Paquete P10', 22, 30000, 1, '2025-12-26 01:46:33');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `turnusage`
--

DROP TABLE IF EXISTS `turnusage`;
CREATE TABLE IF NOT EXISTS `turnusage` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `qrCodeId` int(11) NOT NULL,
  `machineId` int(11) NOT NULL,
  `usedAt` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `qrCodeId` (`qrCodeId`),
  KEY `machineId` (`machineId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `users`
--

DROP TABLE IF EXISTS `users`;
CREATE TABLE IF NOT EXISTS `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `password` varchar(255) NOT NULL,
  `role` enum('admin','cajero','admin_restaurante','socio') NOT NULL,
  `createdBy` int(11) DEFAULT NULL,
  `createdAt` datetime DEFAULT current_timestamp(),
  `notes` text DEFAULT NULL,
  `isActive` tinyint(1) DEFAULT 1,
  `local` varchar(100) DEFAULT 'El Mekatiadero',
  `updatedAt` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `users`
--

INSERT INTO `users` (`id`, `name`, `password`, `role`, `createdBy`, `createdAt`, `notes`, `isActive`, `local`, `updatedAt`) VALUES
(1, 'Carlos Andrés Castro', '1000414625', 'admin', NULL, '2025-12-26 01:46:33', '', 1, 'El Mekatiadero', '2025-12-27 00:19:22'),
(2, 'Andrés Felipe Gomez', '987654321', 'admin', 1, '2025-12-26 01:46:33', NULL, 1, 'El Mekatiadero', '2025-12-27 00:19:22'),
(3, 'Carolina', '999999', 'cajero', 1, '2025-12-26 01:46:33', '', 0, 'El Mekatiadero', '2025-12-30 19:59:33'),
(4, 'Camila', '392817392', 'admin_restaurante', 1, '2025-12-26 01:46:33', NULL, 1, 'El Mekatiadero', '2025-12-27 00:19:22'),
(5, 'Prueba', '252525', 'admin', 1, '2025-12-26 18:44:56', '', 1, 'El Mekatiadero', '2025-12-27 00:19:22'),
(6, 'Juan Socio', '666666', 'socio', 1, '2025-12-30 14:58:57', '', 1, 'El Mekatiadero', '2025-12-30 19:58:57');

-- --------------------------------------------------------

--
-- Estructura de tabla para la tabla `userturns`
--

DROP TABLE IF EXISTS `userturns`;
CREATE TABLE IF NOT EXISTS `userturns` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `qr_code_id` int(11) NOT NULL,
  `turns_remaining` int(11) NOT NULL DEFAULT 0,
  `total_turns` int(11) NOT NULL DEFAULT 0,
  `package_id` int(11) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_qr_code` (`qr_code_id`),
  KEY `package_id` (`package_id`)
) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Volcado de datos para la tabla `userturns`
--

INSERT INTO `userturns` (`id`, `qr_code_id`, `turns_remaining`, `total_turns`, `package_id`, `created_at`, `updated_at`) VALUES
(1, 1, 20, 20, 9, '2025-12-26 14:05:30', '2025-12-26 14:05:30'),
(2, 2, 12, 12, 5, '2025-12-26 14:15:16', '2025-12-26 14:15:16'),
(3, 3, 4, 4, 1, '2025-12-26 14:47:25', '2025-12-26 14:47:25'),
(4, 4, 6, 6, 2, '2025-12-26 15:28:42', '2025-12-26 15:28:42'),
(5, 5, 18, 18, 8, '2025-12-26 15:30:02', '2025-12-26 15:30:02'),
(6, 6, 18, 18, 8, '2025-12-26 15:35:23', '2025-12-26 15:35:23'),
(7, 7, 12, 12, 5, '2025-12-26 16:03:49', '2025-12-26 16:03:49'),
(8, 8, 20, 20, 9, '2025-12-26 16:39:06', '2025-12-26 16:39:06'),
(9, 9, 6, 6, 2, '2025-12-29 14:30:27', '2025-12-29 14:30:27'),
(10, 10, 10, 10, 4, '2025-12-29 14:36:47', '2025-12-29 14:36:47'),
(11, 11, 22, 22, 10, '2025-12-30 13:49:49', '2025-12-30 13:49:49');

--
-- Restricciones para tablas volcadas
--

--
-- Filtros para la tabla `confirmation`
--
ALTER TABLE `confirmation`
  ADD CONSTRAINT `confirmation_ibfk_1` FOREIGN KEY (`fault_report_id`) REFERENCES `errorreport` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `confirmation_ibfk_2` FOREIGN KEY (`admin_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `confirmation_logs`
--
ALTER TABLE `confirmation_logs`
  ADD CONSTRAINT `confirmation_logs_ibfk_1` FOREIGN KEY (`fault_report_id`) REFERENCES `errorreport` (`id`),
  ADD CONSTRAINT `confirmation_logs_ibfk_2` FOREIGN KEY (`admin_id`) REFERENCES `users` (`id`);

--
-- Filtros para la tabla `errorreport`
--
ALTER TABLE `errorreport`
  ADD CONSTRAINT `errorreport_ibfk_1` FOREIGN KEY (`machineId`) REFERENCES `machine` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `errorreport_ibfk_2` FOREIGN KEY (`userId`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `inversiones`
--
ALTER TABLE `inversiones`
  ADD CONSTRAINT `inversiones_ibfk_1` FOREIGN KEY (`socio_id`) REFERENCES `socios` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `inversiones_ibfk_2` FOREIGN KEY (`maquina_id`) REFERENCES `machine` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `liquidaciones`
--
ALTER TABLE `liquidaciones`
  ADD CONSTRAINT `liquidaciones_ibfk_1` FOREIGN KEY (`maquina_id`) REFERENCES `machine` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `liquidaciones_ibfk_2` FOREIGN KEY (`usuario_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `machine`
--
ALTER TABLE `machine`
  ADD CONSTRAINT `machine_ibfk_1` FOREIGN KEY (`location_id`) REFERENCES `location` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `machinefailures`
--
ALTER TABLE `machinefailures`
  ADD CONSTRAINT `machinefailures_ibfk_1` FOREIGN KEY (`qr_code_id`) REFERENCES `qrcode` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `machinefailures_ibfk_2` FOREIGN KEY (`machine_id`) REFERENCES `machine` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `maquinaporcentajerestaurante`
--
ALTER TABLE `maquinaporcentajerestaurante`
  ADD CONSTRAINT `maquinaporcentajerestaurante_ibfk_1` FOREIGN KEY (`maquina_id`) REFERENCES `machine` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `maquinapropietario`
--
ALTER TABLE `maquinapropietario`
  ADD CONSTRAINT `maquinapropietario_ibfk_1` FOREIGN KEY (`maquina_id`) REFERENCES `machine` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `maquinapropietario_ibfk_2` FOREIGN KEY (`propietario_id`) REFERENCES `propietarios` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `pagoscuotas`
--
ALTER TABLE `pagoscuotas`
  ADD CONSTRAINT `pagoscuotas_ibfk_1` FOREIGN KEY (`socio_id`) REFERENCES `socios` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `qrcode`
--
ALTER TABLE `qrcode`
  ADD CONSTRAINT `fk_qrcode_turnpackage` FOREIGN KEY (`turnPackageId`) REFERENCES `turnpackage` (`id`) ON DELETE SET NULL,
  ADD CONSTRAINT `qrcode_ibfk_1` FOREIGN KEY (`turnPackageId`) REFERENCES `turnpackage` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `rendimientos`
--
ALTER TABLE `rendimientos`
  ADD CONSTRAINT `rendimientos_ibfk_1` FOREIGN KEY (`socio_id`) REFERENCES `socios` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `rendimientos_ibfk_2` FOREIGN KEY (`maquina_id`) REFERENCES `machine` (`id`) ON DELETE SET NULL;

--
-- Filtros para la tabla `resolucionreportes`
--
ALTER TABLE `resolucionreportes`
  ADD CONSTRAINT `resolucionreportes_ibfk_1` FOREIGN KEY (`error_report_id`) REFERENCES `errorreport` (`id`),
  ADD CONSTRAINT `resolucionreportes_ibfk_2` FOREIGN KEY (`admin_id`) REFERENCES `users` (`id`);

--
-- Filtros para la tabla `sessionlog`
--
ALTER TABLE `sessionlog`
  ADD CONSTRAINT `sessionlog_ibfk_1` FOREIGN KEY (`userId`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `turnusage`
--
ALTER TABLE `turnusage`
  ADD CONSTRAINT `turnusage_ibfk_1` FOREIGN KEY (`qrCodeId`) REFERENCES `qrcode` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `turnusage_ibfk_2` FOREIGN KEY (`machineId`) REFERENCES `machine` (`id`) ON DELETE CASCADE;

--
-- Filtros para la tabla `userturns`
--
ALTER TABLE `userturns`
  ADD CONSTRAINT `userturns_ibfk_1` FOREIGN KEY (`qr_code_id`) REFERENCES `qrcode` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `userturns_ibfk_2` FOREIGN KEY (`package_id`) REFERENCES `turnpackage` (`id`) ON DELETE SET NULL;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
