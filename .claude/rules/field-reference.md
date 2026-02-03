# Field ID Reference

This document contains the field ID mappings used with `udfDadoByIdDad(idReq, fieldId)` and `udfRepDad()` functions.

## Location Fields

| ID | Alt ID | Field Name | Description |
|----|--------|------------|-------------|
| 289 | - | Regional | Regional grouping |
| 290 | - | Micro regional | Micro-regional grouping |
| 91 | 304 | UF | State (Unidade Federativa) |
| 140 | 305 | Cidade | City |
| 201 | 306 | POP | Point of Presence |
| 172 | - | Empresa do Polo | Pole company |

## Failure Details

| ID | Field Name | Description |
|----|------------|-------------|
| 286 | Motivo | Primary failure reason |
| 287 | Motivo 2 | Secondary failure reason |
| 313 | Fato | Fact/event description |
| 314 | Causa | Root cause |
| 315 | Acao | Corrective action taken |
| 288 | Detalhamento | Detailed description |
| 102 | Motivo da Falha (Rede) | Network failure reason |

## Time Tracking

| ID | Field Name | Description |
|----|------------|-------------|
| 301 | Downtime | Service downtime duration |
| 302 | Tempo atendimento | Response/resolution time |
| 266 | Data/hora inicio falha | Failure start datetime |
| 267 | Data/hora fim falha | Failure end datetime |
| 293 | Data da Falha | Failure timestamp |
| 294 | Data correcao | Correction timestamp |
| 295 | Data finalizacao | Finalization timestamp |

## Impact Assessment

| ID | Alt ID | Field Name | Description |
|----|--------|------------|-------------|
| 139 | 309 | Clientes impactados | Impacted client count |
| 291 | - | Cliente impactado? | Client impact flag (Sim/Nao) |
| 292 | - | Queda do servico | Service outage flag (Sim/Nao) |
| 340 | - | B2C clients | B2C impacted clients |
| 336 | - | B2B clients | B2B impacted clients |
| 337 | - | Total clients | Total impacted clients |

## Circuit/Capacity Fields (NOC IP)

| ID | Field Name | Description |
|----|------------|-------------|
| 271 | Circuitos de capacidade | Capacity circuits (Point A x Point B) |
| 272 | Parceira | Partner (Capacity Circuit) |
| 273 | Capacidade afetada | Affected capacity |
| 100 | Protocolo | Protocol number |

## Escalation Fields

| ID | Field Name | Description |
|----|------------|-------------|
| 552 | Evento critico? | Critical event flag |
| 559 | Escalonado? | Escalated flag |
| 560 | Nivel do escalonamento | Escalation level |
| 561 | Nome do escalonado | Escalated person name |
| 562 | Telefone do escalonado | Escalated person phone |
| 563 | Data/hora escalonamento | Escalation datetime |
| 564 | Previsao de retorno | Estimated return time |

## Other Fields

| ID | Field Name | Description |
|----|------------|-------------|
| 250 | Deslocamento equipamentos | Equipment deployment required |
| 311 | Tempo deslocamento | Travel time |
| 256 | Necessidade projeto | Project requirement |
| 308 | Problema 100% infra | Infrastructure-only problem |
| 310 | Impacto | Impact level |
| 370 | Atividade resumida | Activity summary |
