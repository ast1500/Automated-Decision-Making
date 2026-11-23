# Safirion Bot — robô de sinais (modo DEMO manual)

Motor de análise técnica, gerenciamento de risco, backtest e painel de monitoramento
para operar **manualmente** na conta DEMO da Safirion (`trade.safirion.com`).

> **O robô não envia ordens para a Safirion.** Não existe API oficial nem integração
> autorizada (ver diagnóstico abaixo). Os sinais são executados por você na conta DEMO
> e o resultado é registrado no robô. A execução automática só será implementada se a
> Safirion autorizar por escrito.

## Diagnóstico da plataforma (Etapas 1–2)

| Item | Situação verificada |
|---|---|
| Base tecnológica | `trade.safirion.com/robots.txt` referencia `iqoption.com/sitemap_index.xml` e `admin.iqoption.com`; os Termos citam a IQ Option. É uma plataforma white-label da IQ Option. |
| Acesso automatizado | `robots.txt` bloqueia `*/traderoom`, `*/trading`, `*/operations`, `*/portfolio`, `*/transactions`; todas as páginas ficam atrás de uma página anti-robô (“One moment, please…”). |
| API oficial | Nenhuma encontrada. Bibliotecas não oficiais (“iqoptionapi”) usam engenharia reversa do WebSocket privado — **não são usadas aqui**. |
| Termos (`safirion.com/legal/terms/`) | Não foi possível ler pelo ambiente automatizado; cláusulas sobre automação/terceiros ainda precisam ser conferidas no texto. |
| Cotações | O feed da Safirion (incluindo ativos OTC) é interno. O robô usa dados públicos (Kraken/Binance) como aproximação. |

## Arquitetura

```
safirion_bot/
  indicators.py   EMA 9/21/50/200, RSI 14, MACD, Bollinger, ATR, ADX (+DI/−DI)
  patterns.py     padrões de candles, suportes e resistências (pivôs agrupados)
  analysis.py     tendência, consolidação, rompimento, reversão, score 0–100, sinal
  risk.py         1% por operação, 10/dia, 3 perdas seguidas, −5% / +3% diário, sem Gale
  settlement.py   liquidação simulada (digital com expiração ou forex com SL/TP)
  engine.py       ciclo: dados → liquidação → análise → risco → registro
  backtest.py     backtest candle a candle com as mesmas regras
  db.py           SQLite (data/safirion.db)
  dashboard.py    painel estático (docs/index.html)
config.json       ativos, timeframes, payout, estratégia e risco (sem senhas)
```

### Score (0–100)

Para cada direção soma-se: EMAs alinhadas (25), ADX/DI (15), MACD (15), RSI em zona de
momento (15), Bollinger (10), distância/rompimento de S/R (10), padrão de candle (10).
Um setup alternativo de **reversão** pontua toque em S/R (30), RSI extremo (25), candle de
reversão (25) e banda de Bollinger (20). Em consolidação sem rompimento o score cai 40%.
Sinal só sai com score ≥ `min_score` (70) e vantagem ≥ `min_margin` (15) sobre a direção oposta.

Modalidades (`strategy.modality`): `digital` (compra = acima / venda = abaixo, expiração em
`expiry_candles`) e `forex` (stop `sl_atr`×ATR, alvo `tp_atr`×ATR).

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q                         # testes
python -m safirion_bot analyze BTCUSD M15   # análise instantânea
python -m safirion_bot run                  # um ciclo completo + painel
python -m safirion_bot backtest BTCUSD M15 --candles 6000
python -m safirion_bot confirm-demo         # declara que opera só na DEMO
python -m safirion_bot set-balance 10000    # sincroniza com o saldo exibido na DEMO
python -m safirion_bot record 7 win 85 --screenshot prints/7.png   # resultado real da DEMO
```

Fluxo de uma operação: o ciclo registra a operação sugerida (`OPEN`) → você executa na DEMO
→ o robô liquida um resultado *simulado* com dados públicos → `record` substitui pelo
resultado real e ajusta o saldo.

## Ambiente de execução (sem VPS)

* A sessão de nuvem do Claude é efêmera: não mantém processo 24/7.
* `.github/workflows/signals.yml` executa um ciclo a cada 15 min no GitHub Actions e grava
  `data/safirion.db` e `docs/index.html` no repositório (estado recuperável após reinícios).
  O agendamento só funciona depois que o workflow estiver no branch padrão; atrasos de vários
  minutos são comuns, por isso M1 não é viável e M5 pode perder sinais (sinais com mais de um
  candle de atraso são descartados).
* O painel pode ser publicado com GitHub Pages (pasta `docs/`).

## Resultados de backtest (dados públicos, payout 85%, expiração 3 candles)

| Mercado | Período | Operações | Acerto | Equilíbrio | Retorno | DD máx. |
|---|---|---|---|---|---|---|
| BTC/USD M5 | 03–23/11/2026 | 161 | 46,6% | 54,0% | −20,5% | 25,1% |
| BTC/USD M15 | 22/09–23/11/2026 | 403 | 44,7% | 54,0% | −51,2% | 51,2% |
| EUR/USD M15 | 16–23/11/2026 | 32 | 50,0% | 54,0% | −2,5% | 7,2% |

**A estratégia atual não tem vantagem estatística**: o acerto fica abaixo do necessário para
empatar com payout de 85%. Os setups de rompimento foram os piores (≈38% em BTC M15).
Não use estes sinais com dinheiro real.
