# 🚀 MSSQL MCP Server v0.2.0 - Guía de Uso

## 📦 Instalación

El servidor ya está instalado y listo para usar. Versión actual: **0.2.0**

```bash
# Verificar instalación
pip show microsoft_sql_server_mcp

# Reinstalar si es necesario
cd c:\Users\BryanR\Documents\GitHub\mssql_mcp_server
pip install -e .
```

---

## ⚙️ Configuración

### Opción 1: Variables de Entorno (Rápido)

```bash
# Básico
set MSSQL_SERVER=localhost
set MSSQL_DATABASE=mydb
set MSSQL_USER=admin
set MSSQL_PASSWORD=mypassword
```

### Opción 2: Archivo YAML (Recomendado)

Crear `mssql_config.yml` en el directorio del proyecto:

```yaml
# Conexión a la base de datos
server: localhost
database: mydb
user: admin
password: mypassword
port: 1433

# Connection Pool - 50-70% más rápido
connection_pool:
  min_size: 2      # Conexiones mínimas siempre activas
  max_size: 10     # Máximo de conexiones permitidas
  timeout: 30      # Timeout para obtener conexión (segundos)

# Query Cache - Queries repetidas casi instantáneas
cache:
  enabled: true    # Habilitar cache
  ttl: 300        # Tiempo de vida en segundos (5 min)
  max_size: 1000  # Máximo de queries en cache

# Rate Limiting - Protección contra abuso
  enabled: true      # Habilitar rate limiting
  max_requests: 100  # Máximo de requests por ventana
  window_seconds: 60 # Tamaño de ventana en segundos
```

**Nota:** Las variables de entorno tienen precedencia sobre el archivo YAML.

---

## 🎯 Uso con Claude Desktop

Actualizar `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uvx",
      "args": ["microsoft_sql_server_mcp"],
      "env": {
        "MSSQL_SERVER": "localhost",
        "MSSQL_DATABASE": "your_database",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password"
      }
    }
  }
}
```

O usando Python directamente:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "python",
      "args": ["-m", "mssql_mcp_server"],
      "env": {
        "MSSQL_SERVER": "localhost",
        "MSSQL_DATABASE": "your_database"
      }
    }
  }
}
```

---

## 🛠️ Herramientas Disponibles

### 1. `execute_sql` - Query SQL Mejorado

**Características nuevas:**
- ✅ Caching automático
- ✅ Paginación
- ✅ Múltiples formatos (CSV, Table, JSON)
- ✅ Estimación de complejidad

**Ejemplo básico:**
```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM users WHERE active = 1"
  }
}
```

**Con cache:**
```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM products WHERE category = 'Electronics'",
    "use_cache": true
  }
}
```

**Con paginación:**
```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM orders ORDER BY created_at DESC",
    "page": 2,
    "page_size": 50
  }
}
```

**Formato Table:**
```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT id, name, email FROM users LIMIT 10",
    "format": "table"
  }
}
```

---

### 2. `execute_transaction` ✨ NUEVO - Transacciones

**Ejecuta múltiples queries de forma atómica (todas o ninguna).**

**Ejemplo:**
```json
{
  "tool": "execute_transaction",
  "arguments": {
    "queries": [
      "UPDATE inventory SET quantity = quantity - 5 WHERE product_id = 123",
      "INSERT INTO orders (product_id, quantity, total) VALUES (123, 5, 499.95)",
      "INSERT INTO audit_log (action, details) VALUES ('ORDER_CREATED', 'Product 123 x5')"
    ],
    "timeout": 60
  }
}
```

**Respuesta exitosa:**
```
✅ Transaction COMMITTED successfully
Queries executed: 3
Total duration: 234.50ms

Results:
  Query 0: 1 rows affected
  Query 1: 1 rows affected
  Query 2: 1 rows affected
```

**Respuesta con error:**
```
❌ Transaction failed and was rolled back
Error: Query 1 failed: Syntax error...
```

---

### 3. `execute_stored_procedure` ✨ NUEVO - Stored Procedures

**Ejecuta stored procedures con parámetros.**

**Ejemplo simple:**
```json
{
  "tool": "execute_stored_procedure",
  "arguments": {
    "procedure_name": "dbo.GetActiveUsers"
  }
}
```

**Con parámetros:**
```json
{
  "tool": "execute_stored_procedure",
  "arguments": {
    "procedure_name": "dbo.GetUsersByRole",
    "parameters": {
      "role": "admin",
      "active_only": true,
      "min_login_count": 10
    },
    "timeout": 30
  }
}
```

**Respuesta:**
```
✅ Stored procedure executed: dbo.GetUsersByRole
Result sets: 2
Duration: 125.30ms

Results:
  Result Set 0:
    Columns: id, username, email, last_login
    Rows: 15

  Result Set 1:
    Columns: total_count
    Rows: 1
```

---

### 4. `list_stored_procedures` ✨ NUEVO

**Lista todos los stored procedures disponibles.**

**Ejemplo:**
```json
{
  "tool": "list_stored_procedures"
}
```

**Con filtro por schema:**
```json
{
  "tool": "list_stored_procedures",
  "arguments": {
    "schema": "dbo"
  }
}
```

**Respuesta:**
```
Stored Procedures:

  dbo.ProcessMonthlyReports (Schema: dbo)
  dbo.GetUsersByRole (Schema: dbo)
  dbo.UpdateInventory (Schema: dbo)
  sales.GetQuarterlyStats (Schema: sales)
```

---

### 5. `describe_table` - Información de Esquema

**Obtiene información detallada de una tabla.**

**Ejemplo:**
```json
{
  "tool": "describe_table",
  "arguments": {
    "table_name": "dbo.users"
  }
}
```

**Respuesta:**
```
Schema for dbo.users:

Column Name | Data Type | Max Length | Nullable | Default | Primary Key
--------------------------------------------------------------------------------
id          | int       | N/A        | NO       | N/A     | YES
username    | varchar   | 50         | NO       | N/A     | NO
email       | varchar   | 100        | NO       | N/A     | NO
created_at  | datetime  | N/A        | YES      | GETDATE | NO
active      | bit       | N/A        | NO       | 1       | NO
```

---

### 6. `get_pool_stats` - Estadísticas de Connection Pool

**Monitorea el estado del pool de conexiones.**

```json
{
  "tool": "get_pool_stats"
}
```

**Respuesta:**
```
Connection Pool Statistics:
  Total Connections: 5
  Available: 3
  In-Use: 2
  Pool Size: 2-10
  Status: Active
```

---

### 7. `get_cache_stats` ✨ NUEVO - Estadísticas de Cache

**Monitorea el rendimiento del cache.**

```json
{
  "tool": "get_cache_stats"
}
```

**Respuesta:**
```
Query Cache Statistics:
  Enabled: True
  Size: 234/1000
  TTL: 300s
  Hits: 1,456
  Misses: 234
  Hit Rate: 86.15%
  Evictions: 12
```

**¿Qué significa?**
- **Hit Rate 86%** = 86% de queries sirvieron desde cache (super rápido!)
- **Evictions** = Queries removidas al llenarse el cache

---

### 8. `clear_cache` ✨ NUEVO - Limpiar Cache

**Limpia todo el cache manualmente.**

```json
{
  "tool": "clear_cache"
}
```

**Respuesta:**
```
✅ Cache cleared successfully
```

**Cuándo usar:**
- Después de actualizar muchos datos
- Para forzar refresh de datos
- Si sospechas datos obsoletos en cache

---


**Verifica cuántas requests quedan disponibles.**

```json
{
}
```

**Respuesta:**
```
Rate Limit Statistics:
  Request Count: 45/100
  Remaining: 55
  Window: 60s
```

**Si excedes el límite:**
```
Rate Limit Statistics:
  Request Count: 100/100
  Remaining: 0
  Window: 60s
  Retry After: 23s
```

---

## 📊 Ejemplos de Casos de Uso

### Caso 1: Reporte con Cache

**Primera ejecución:**
```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT category, SUM(sales) as total FROM products GROUP BY category",
    "use_cache": true,
    "format": "table"
  }
}
```

**Respuesta:** ~150ms

**Segunda ejecución (misma query):**

**Respuesta:** ~2ms 💾 [FROM CACHE]

**Beneficio:** 98% más rápido!

---

### Caso 2: Actualización Transaccional

```json
{
  "tool": "execute_transaction",
  "arguments": {
    "queries": [
      "BEGIN TRANSACTION",
      "UPDATE accounts SET balance = balance - 1000 WHERE account_id = 'ACC001'",
      "UPDATE accounts SET balance = balance + 1000 WHERE account_id = 'ACC002'",
      "INSERT INTO transfers (from_account, to_account, amount) VALUES ('ACC001', 'ACC002', 1000)",
      "COMMIT"
    ]
  }
}
```

**Si falla cualquier query:** Todas se revierten (rollback automático)

---

### Caso 3: Paginación de Resultados Grandes

```json
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM large_table ORDER BY id",
    "page": 1,
    "page_size": 100
  }
}
```

**Página 1:** Filas 1-100  
**Página 2:** Filas 101-200  
**Página 3:** Filas 201-300

---

### Caso 4: Stored Procedure para Reportes

```json
{
  "tool": "execute_stored_procedure",
  "arguments": {
    "procedure_name": "dbo.GenerateMonthlyReport",
    "parameters": {
      "year": 2025,
      "month": 12,
      "department": "Sales"
    }
  }
}
```

---

## 🎓 Best Practices

### 1. **Usa Cache para Queries Repetidas**

❌ **No:**
```json
// Sin cache - 200ms cada vez
{"tool": "execute_sql", "arguments": {"query": "SELECT * FROM dashboard_stats"}}
```

✅ **Sí:**
```json
// Con cache - 2ms segunda vez
{"tool": "execute_sql", "arguments": {"query": "SELECT * FROM dashboard_stats", "use_cache": true}}
```

---

### 2. **Usa Transacciones para Operaciones Relacionadas**

❌ **No:**
```json
// Query 1
{"tool": "execute_sql", "arguments": {"query": "UPDATE inventory..."}}

// Query 2
{"tool": "execute_sql", "arguments": {"query": "INSERT INTO orders..."}}

// Si Query 2 falla, Query 1 ya se ejecutó!
```

✅ **Sí:**
```json
{
  "tool": "execute_transaction",
  "arguments": {
    "queries": [
      "UPDATE inventory...",
      "INSERT INTO orders..."
    ]
  }
}
// Ambas OK o ambas rollback
```

---

### 3. **Usa Paginación para Tablas Grandes**

❌ **No:**
```json
// Retorna 1,000,000 rows - LENTO y consume memoria
{"tool": "execute_sql", "arguments": {"query": "SELECT * FROM huge_table"}}
```

✅ **Sí:**
```json
// Solo 100 rows por vez
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM huge_table ORDER BY id",
    "page": 1,
    "page_size": 100
  }
}
```

---

### 4. **Limpia Cache Después de Actualizaciones Masivas**

```json
// 1. Actualizar datos
{
  "tool": "execute_sql",
  "arguments": {
    "query": "UPDATE products SET price = price * 1.1"
  }
}

// 2. Limpiar cache
{
  "tool": "clear_cache"
}
```

---

### 5. **Monitorea el Pool de Conexiones**

```json
{
  "tool": "get_pool_stats"
}
```

**Si ves:**
- `In-Use: 9/10` → Considera aumentar `max_size`
- `Available: 0` → Pool exhausto, ajusta configuración

---

## 🐛 Troubleshooting

### "Rate limit exceeded"

**Causa:** Has excedido el límite de requests (default: 100/minuto)

**Solución:**
1. Espera el tiempo indicado en `retry_after`
2. O aumenta el límite en configuración:

```yaml
  max_requests: 200  # Aumentar
  window_seconds: 60
```

---

### "Connection pool exhausted"

**Causa:** Todas las conexiones del pool están en uso

**Solución:**
```yaml
connection_pool:
  max_size: 20  # Aumentar de 10 a 20
```

---

### Queries lentas con cache habilitado

**Causa:** Cache puede estar deshabilitado o lleno

**Verifica:**
```json
{"tool": "get_cache_stats"}
```

**Si `Enabled: False`:**
```yaml
cache:
  enabled: true
```

**Si `Size: 1000/1000` (lleno):**
```yaml
cache:
  max_size: 5000  # Aumentar
```

---

### Error: "Invalid table name"

**Causa:** Nombre de tabla con caracteres no permitidos

**Solución:**
- Usa solo: letras, números, guión bajo
- Para schema: `dbo.users` ✅
- Evita: `user-table` ❌

---

## 📈 Monitoreo y Métricas

### Dashboard de Monitoreo (Recomendado)

Ejecuta periódicamente:

```bash
# 1. Connection Pool
{"tool": "get_pool_stats"}

# 2. Cache Performance
{"tool": "get_cache_stats"}

# 3. Rate Limiting
```

### Métricas Importantes

**Connection Pool:**
- `In-Use` < 80% de `max_size` = ✅ OK
- `In-Use` > 90% de `max_size` = ⚠️ Aumentar pool

**Cache:**
- `Hit Rate` > 70% = ✅ Excelente
- `Hit Rate` 40-70% = ✅ Bueno
- `Hit Rate` < 40% = ⚠️ Revisar TTL o queries

**Rate Limiting:**
- `Remaining` > 20% = ✅ OK
- `Remaining` < 10% = ⚠️ Cerca del límite

---

## 🚀 Próximos Pasos

### Para Desarrollo:

1. **Tests:**
   ```bash
   cd c:\Users\BryanR\Documents\GitHub\mssql_mcp_server
   pytest tests/
   ```

2. **Personalizar configuración:**
   - Editar `mssql_config.yml`
   - Ajustar pool, cache, rate limiting según tu carga

3. **Monitorear rendimiento:**
   - Ejecutar queries con cache
   - Verificar hit rate
   - Ajustar TTL si es necesario

### Para Producción:

1. **Seguridad:**
   - Usar usuario SQL con permisos mínimos (no sa/admin)
   - Habilitar `MSSQL_ENCRYPT=true`
   - Windows Authentication si es posible

2. **Rendimiento:**
   - Aumentar `connection_pool.max_size` según carga
   - Habilitar cache con TTL apropiado
   - Ajustar `page_size` según necesidad

3. **Monitoring:**
   - Configurar auditoría en archivo separado
   - Revisar logs de seguridad periódicamente
   - Monitorear métricas de pool y cache

---

## 📚 Recursos

- **README.md** - Documentación general
- **CHANGELOG.md** - Historial de cambios
- **mssql_config.example.yml** - Ejemplo de configuración
- **plan-de-mejoras.md** - Plan completo de mejoras
- **resumen-completo-mejoras-v0.2.0.md** - Este documento

---

## 🆘 Soporte

Si encuentras problemas:

1. Revisa los logs del servidor
2. Verifica la configuración en `mssql_config.yml`
3. Comprueba las credenciales de base de datos
4. Revisa las estadísticas de pool/cache/rate limit

---

**Versión:** 0.2.0  
**Última actualización:** 2025-12-31  
**Estado:** Production-Ready 🚀
