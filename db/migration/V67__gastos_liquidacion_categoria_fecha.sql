-- V67: reconciliar gastos_liquidacion al esquema que usa liquidaciones v2.
-- El frontend manda y muestra categoria + fecha por gasto, pero la tabla/endpoint
-- no los guardaban → la columna FECHA salía "undefined" y la categoría caía a 'otro'.
-- Esta tabla ya NO la usa el módulo de socios (solo liquidaciones), así que es
-- seguro agregar columnas y relajar las columnas legacy NOT NULL para que el
-- INSERT (concepto, monto, usuario_id, categoria, fecha) funcione en todo entorno.
-- Patrón condicional. NUNCA usar ; dentro de un string.

-- usuario_id (en algunos entornos la tabla legacy no lo tenía)
SET @col_uid = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'usuario_id'
);
SET @ddl_uid = IF(@col_uid = 0,
  'ALTER TABLE gastos_liquidacion ADD COLUMN usuario_id INT NULL',
  'SELECT 1'
);
PREPARE s_uid FROM @ddl_uid; EXECUTE s_uid; DEALLOCATE PREPARE s_uid;

-- categoria
SET @col_cat = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'categoria'
);
SET @ddl_cat = IF(@col_cat = 0,
  'ALTER TABLE gastos_liquidacion ADD COLUMN categoria VARCHAR(30) NULL DEFAULT ''otro''',
  'SELECT 1'
);
PREPARE s_cat FROM @ddl_cat; EXECUTE s_cat; DEALLOCATE PREPARE s_cat;

-- fecha (del gasto)
SET @col_fec = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'fecha'
);
SET @ddl_fec = IF(@col_fec = 0,
  'ALTER TABLE gastos_liquidacion ADD COLUMN fecha DATE NULL',
  'SELECT 1'
);
PREPARE s_fec FROM @ddl_fec; EXECUTE s_fec; DEALLOCATE PREPARE s_fec;

-- Relajar columnas legacy NOT NULL (solo si existen como NOT NULL) para que el
-- INSERT v2 no falle. No las usa ningún código actual.
SET @nn_prop = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'propietario_id'
    AND IS_NULLABLE = 'NO'
);
SET @ddl_prop = IF(@nn_prop = 1,
  'ALTER TABLE gastos_liquidacion MODIFY COLUMN propietario_id INT NULL',
  'SELECT 1'
);
PREPARE s_prop FROM @ddl_prop; EXECUTE s_prop; DEALLOCATE PREPARE s_prop;

SET @nn_fi = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'fecha_inicio'
    AND IS_NULLABLE = 'NO'
);
SET @ddl_fi = IF(@nn_fi = 1,
  'ALTER TABLE gastos_liquidacion MODIFY COLUMN fecha_inicio DATE NULL',
  'SELECT 1'
);
PREPARE s_fi FROM @ddl_fi; EXECUTE s_fi; DEALLOCATE PREPARE s_fi;

SET @nn_ff = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'fecha_fin'
    AND IS_NULLABLE = 'NO'
);
SET @ddl_ff = IF(@nn_ff = 1,
  'ALTER TABLE gastos_liquidacion MODIFY COLUMN fecha_fin DATE NULL',
  'SELECT 1'
);
PREPARE s_ff FROM @ddl_ff; EXECUTE s_ff; DEALLOCATE PREPARE s_ff;
