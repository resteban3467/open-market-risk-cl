# CMF RAN 21-7 — Regulatory Band & Disallowance Reference Tables

## 1. General Interest Rate Risk — Maturity Method (13 Bands)

| Zone | Band ID | Coupon >= 3% (Residual Maturity) | Coupon < 3% / Zero-Coupon (Residual Maturity) | Assumed Yield Shock (bps) | Sensitivity Weight ($W_b$) | Vertical Disallowance ($v_b$) |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: |
| **Zone 1** | **1** | `<= 1 month` (`<= 0.0833 yr`) | `<= 1 month` (`<= 0.0833 yr`) | 100 | `0.0000` (0.00%) | 10% |
| **Zone 1** | **2** | `> 1M to <= 3M` (`0.25 yr`) | `> 1M to <= 3M` (`0.25 yr`) | 100 | `0.0020` (0.20%) | 10% |
| **Zone 1** | **3** | `> 3M to <= 6M` (`0.50 yr`) | `> 3M to <= 6M` (`0.50 yr`) | 100 | `0.0040` (0.40%) | 10% |
| **Zone 1** | **4** | `> 6M to <= 12M` (`1.00 yr`) | `> 6M to <= 12M` (`1.00 yr`) | 100 | `0.0070` (0.70%) | 10% |
| **Zone 2** | **5** | `> 1Y to <= 2Y` | `> 1.0Y to <= 1.9Y` | 90 | `0.0125` (1.25%) | 10% |
| **Zone 2** | **6** | `> 2Y to <= 3Y` | `> 1.9Y to <= 2.8Y` | 80 | `0.0175` (1.75%) | 10% |
| **Zone 2** | **7** | `> 3Y to <= 4Y` | `> 2.8Y to <= 3.6Y` | 75 | `0.0225` (2.25%) | 10% |
| **Zone 3** | **8** | `> 4Y to <= 5Y` | `> 3.6Y to <= 4.3Y` | 75 | `0.0275` (2.75%) | 10% |
| **Zone 3** | **9** | `> 5Y to <= 7Y` | `> 4.3Y to <= 5.7Y` | 70 | `0.0325` (3.25%) | 10% |
| **Zone 3** | **10** | `> 7Y to <= 10Y` | `> 5.7Y to <= 7.3Y` | 65 | `0.0375` (3.75%) | 10% |
| **Zone 3** | **11** | `> 10Y to <= 15Y` | `> 7.3Y to <= 9.3Y` | 60 | `0.0450` (4.50%) | 10% |
| **Zone 3** | **12** | `> 15Y to <= 20Y` | `> 9.3Y to <= 10.6Y` | 60 | `0.0525` (5.25%) | 10% |
| **Zone 3** | **13** | `> 20Y` | `> 10.6Y` | 60 | `0.0600` (6.00%) | 10% |

> **Implementation Note:** Because our engine decomposes every instrument into individual zero-coupon cashflows (`fct_valued_cashflows`), each decomposed flow is treated under the **Zero-Coupon / Coupon < 3% column** when using the Maturity Method, OR allocated by its exact `modified_duration` under the 15-band Duration Method.

## 2. Horizontal Disallowance Factors Matrix

| Offset Level | Scope | Disallowance Factor |
| :--- | :--- | :---: |
| **Intra-Zone 1** | Bands 1 to 4 | `0.40` (40%) |
| **Intra-Zone 2** | Bands 5 to 7 | `0.30` (30%) |
| **Intra-Zone 3** | Bands 8 to 13 | `0.30` (30%) |
| **Inter-Zone Adjacent (1 <-> 2)** | Zone 1 vs Zone 2 | `0.40` (40%) |
| **Inter-Zone Adjacent (2 <-> 3)** | Zone 2 vs Zone 3 | `0.40` (40%) |
| **Inter-Zone Non-Adjacent (1 <-> 3)** | Zone 1 vs Zone 3 | `1.00` (100%) |
