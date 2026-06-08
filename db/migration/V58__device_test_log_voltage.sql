ALTER TABLE device_test_log
    ADD COLUMN voltage_mv SMALLINT NULL COMMENT 'Voltaje en milivoltios (ej: 3300 = 3.3V, 5000 = 5V)';
