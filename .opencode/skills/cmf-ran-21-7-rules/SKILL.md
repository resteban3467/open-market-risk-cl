---
name: cmf-ran-21-7-rules
description: Domain rules for Chilean CMF RAN Chapter 21-7 (Market Risk Capital Requirements / R07 report), covering General Interest Rate Risk maturity/duration bands, vertical/horizontal disallowances across Zones 1-3, FX risk (8%), and APRM calculation.
---

# CMF RAN 21-7 & Normative R07 Regulatory Skill

## Purpose
Prevent LLM hallucinations that confuse international Basel IV / FRTB Internal Models with the **Chilean CMF RAN Chapter 21-7 Standardized Approach** (*Método Estándar para Riesgo de Mercado y Archivo Normativo R07*).

## 1. Mandatory Reference Loading
Whenever implementing or testing code in `packages/cmf-regulatory-r07`, you MUST read `.opencode/skills/cmf-ran-21-7-rules/references/ran_21_7_tables.md` to obtain the exact maturity bands, sensitivity weights, and vertical/horizontal disallowance percentages.

## 2. Core RAN 21-7 Calculation Pipeline (General Interest Rate Risk)
1. **Currency & Readjustment Separation:**
   - Interest rate risk MUST be calculated **separately for each currency / readjustment unit** (`CLP`, `UF`, `USD`, etc.). Never offset long/short positions across different currencies inside the maturity ladder.
   - All cashflows (`fct_valued_cashflows`) are decomposed into individual zero-coupon present values in CLP (`present_value_clp`).
   - Active legs (`leg_type == "ACTIVE"` / long fixed-rate asset) enter with positive sign (`+PV`); Passive legs (`leg_type == "PASSIVE"` / short or liability leg) enter with negative sign (`-PV`).

2. **Band Allocation (Maturity Method — 13 Bands):**
   - Allocate each valued cashflow to one of the 13 regulatory bands based on its residual maturity `yf` (`payment_date - valuation_date`) and coupon level (`coupon >= 3%` vs `coupon < 3%` / zero-coupon).
   - Multiply each cashflow's `present_value_clp` by the regulatory sensitivity weight $W_b$ of its band to obtain the **Weighted Long Position** ($L_b$) and **Weighted Short Position** ($S_b$) per band $b \in \{1..13\}$.

3. **Step 1 — Vertical Offset within each Band ($b$):**
   - Matched position in band $b$: $M_b = \min(L_b, S_b)$
   - Net open position in band $b$: $N_b = L_b - S_b$ (positive if net long, negative if net short)
   - **Vertical Disallowance Charge:** $\text{Charge}_{\text{vert}} = \sum_{b=1}^{13} v_b \cdot M_b$, where $v_b = 10\%$ under the Maturity Method ($5\%$ under Duration Method).

4. **Step 2 — Horizontal Offset within each Zone ($z \in \{1, 2, 3\}$):**
   - For each Zone $z$, sum all net long band positions $NL_z = \sum_{b \in z, N_b > 0} N_b$ and net short band positions $NS_z = \sum_{b \in z, N_b < 0} \vert{}N_b\vert{}$.
   - Matched position within Zone $z$: $MZ_z = \min(NL_z, NS_z)$.
   - Net open position of Zone $z$: $NZ_z = NL_z - NS_z$.
   - **Intra-Zone Horizontal Charge:**
     - Zone 1 (Bands 1–4): $40\% \times MZ_1$
     - Zone 2 (Bands 5–7): $30\% \times MZ_2$
     - Zone 3 (Bands 8–13): $30\% \times MZ_3$

5. **Step 3 — Horizontal Offset between Zones (1<->2, 2<->3, and 1<->3):**
   - **Between Zone 1 and Zone 2:** If $NZ_1$ and $NZ_2$ have opposite signs, matched amount $M_{12} = \min(\vert{}NZ_1\vert{}, \vert{}NZ_2\vert{})$. Charge $= 40\% \times M_{12}$. Update residual zone positions $NZ_1', NZ_2'$.
   - **Between Zone 2 and Zone 3:** If residual $NZ_2'$ and $NZ_3$ have opposite signs, matched amount $M_{23} = \min(\vert{}NZ_2'\vert{}, \vert{}NZ_3\vert{})$. Charge $= 40\% \times M_{23}$. Update residual zone positions $NZ_2'', NZ_3'$.
   - **Between Zone 1 and Zone 3:** If residual $NZ_1'$ and $NZ_3'$ have opposite signs, matched amount $M_{13} = \min(\vert{}NZ_1'\vert{}, \vert{}NZ_3'\vert{})$. Charge $= 100\% \times M_{13}$. Update final unmatched residuals $NZ_1''', NZ_2'', NZ_3''$.

6. **Step 4 — Total General Interest Rate Capital Charge (per currency $c$):**
   $$\text{K}_{\text{GIRR}, c} = \text{Charge}_{\text{vert}} + \text{Charge}_{\text{intra-zone}} + \text{Charge}_{\text{inter-zone}} + \vert{}NZ_1'''\vert{} + \vert{}NZ_2''\vert{} + \vert{}NZ_3''\vert{}$$

7. **FX Risk Charge (Riesgo de Moneda Extranjera) & Total APRM:**
   - Calculate net open position in each foreign currency (e.g., `USD`, `EUR` — note: in Chile, `UF` is treated under interest rate/readjustment ladders; check specific R07 configuration parameter `include_uf_in_fx: bool = False`).
   - Apply the **Shorthand Method (Método Simplificado)**:
     $$\text{Net FX Exposure} = \max\left(\sum \text{Net Long FX}_{\text{CLP}}, \sum \vert{}\text{Net Short FX}_{\text{CLP}}\vert{}\right) + \vert{}\text{Net Gold}_{\text{CLP}}\vert{}$$
     $$\text{K}_{\text{FX}} = 0.08 \times \text{Net FX Exposure}$$
   - **Total Market Risk Capital Charge:** $\text{K}_{\text{Total}} = \sum_c \text{K}_{\text{GIRR}, c} + \text{K}_{\text{FX}} + \text{K}_{\text{Equity}}$
   - **APRM (Activos Ponderados por Riesgo de Mercado):**
     $$\text{APRM} = 12.5 \times \text{K}_{\text{Total}}$$

## 3. Verification Protocol
Before completing any regulatory module task, run:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
