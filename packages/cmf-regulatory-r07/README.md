# cmf-regulatory-r07

Implementacion del metodo estandar del Capitulo 21-7 de la RAN de la CMF
(Archivo R07): calculo de activos ponderados por riesgo de mercado (APRM) en
CLP, con foco en el **riesgo general de tasas de interes** (seccion `r07_s02`).

## Reglas de arquitectura

- **Zero Pandas**: 100% Polars + Arrow zero-copy.
- **Solo lectura**: abre `RiskDatabase(..., read_only=True)`; el unico paquete
  autorizado a escribir es `portfolio-pricing-engine`.
- **Clean Architecture**: `config/` (tablas normativas), `repository.py`
  (adapter de datos), `domain/` (motor de calculo) y `r07_exporter.py` (salida).

## Tablas normativas (JSON en `config/`)

| Archivo | Tabla CMF |
|---|---|
| `tabla_1_monedas.json` | Tabla 1: Monedas y unidades de cuenta |
| `tabla_114_exposiciones.json` | Tabla 114: Exposiciones riesgo general/especifico |
| `tabla_115_riesgo_especifico.json` | Tabla 115: Riesgo especifico de tasa |
| `tabla_116_bandas_temporales.json` | Tabla 116: 13 bandas temporales |
| `prm_riesgo_general.json` | PRM CLP/UR/MX, descalces y multiplicador 12.5 |

## Flujo

1. `RegulatoryDataRepository` lee `dim_trades`, `dim_legs`,
   `fct_valued_cashflows`, `fct_fx_rates` y `fct_market_factors` en solo lectura.
2. `build_r07_s02` enriquece el portafolio: banda en dias (`bisect_left` sobre
   `r07_temp_band_days`), `id_moneda`, `tipo_moneda`, exposicion (Tabla 114),
   conversion a CLP y PRM.
3. `compute_girr` aplica posicion neta ponderada, ajuste vertical y ajustes
   horizontales intra/inter-zona.
4. `aggregate_aprm` aplica el multiplicador `12.5` para obtener el APRM total.

## Uso

```bash
uv run report-r07 --date 2026-10-30 [--db data/market_risk.duckdb] [--output report.json]
```
