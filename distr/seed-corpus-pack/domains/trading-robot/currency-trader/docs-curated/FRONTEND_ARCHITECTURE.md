# Currency Trader Frontend Architecture

The dashboard is organized around two top-level concerns:

- Trading workspace: live pair inspection, chart timeframes, bot behavior, trade factors, prediction, and memory.
- Settings: provider health, model routing layers, API keys, and free model catalog refresh.

## Feature Layout

```text
apps/dashboard/src/
  App.tsx
  api.ts
  components/
    Metric.tsx
    ModelKeyForm.tsx
    ModelPreferencesForm.tsx
    StatusBadge.tsx
  features/
    settings/
      SettingsView.tsx
    trading/
      analysis.ts
      BotBehaviorOverlay.tsx
      ChartPanel.tsx
      FactorsPanel.tsx
      MemoryPanel.tsx
      PredictionPanel.tsx
      SymbolRail.tsx
      TimeframeSelector.tsx
      TradingTabs.tsx
      TradingViewChart.tsx
      TradingWorkstation.tsx
      tradingView.ts
```

## Trading Flow

`TradingWorkstation` owns the selected tab and selected symbol. It delegates:

- `SymbolRail`: pair selection.
- `TradingTabs`: workspace section navigation.
- `ChartPanel`: real TradingView chart plus timeframe selection.
- `TradingViewChart`: TradingView advanced chart embed.
- `BotBehaviorOverlay`: what the bot is currently planning or waiting for.
- `FactorsPanel`: deterministic entry and exit formulas.
- `PredictionPanel`: current setup interpretation and timing.
- `MemoryPanel`: recent decisions and blocked reasons.

## Settings Flow

`SettingsView` owns provider configuration:

- provider health stays separate from trading operations.
- model routing layers are managed by `ModelPreferencesForm`.
- API keys are managed by `ModelKeyForm`.
- OpenRouter free-model catalog is refreshed explicitly before selecting free fallback routes.

Free OpenRouter models can change. The UI therefore supports:

- `openrouter/free`: OpenRouter's own free router.
- largest-context current free model: selected from the live catalog by context length, not by unverified quality claims.

Trade execution remains deterministic. Model routing is for notes, review, and explanation unless execution policy is explicitly changed.
