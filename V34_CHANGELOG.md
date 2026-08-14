# v34 audit notes

- Added stable client-order-id generation for ambiguous submissions.
- Reconciliation now understands native client-order-id fields exposed by CCXT/exchanges.
- Added exchange-specific submission parameter mapping:
  - Binance: `newClientOrderId`
  - Bybit: `orderLinkId`
  - OKX: `clOrdId`
  - Bitget: `clientOid`
  - MEXC: `clientOrderId`
  - KuCoin: `clientOid`
  - Gate.io: `text`
- Normalized order responses now recover client IDs from CCXT `info` when the unified field is absent.
- Recovery through `fetch_orders()` also checks native client-id fields.
- Added regression coverage for all seven supported exchanges.
