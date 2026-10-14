"""Executable React template packs for the five canonical screen families."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from .contracts import ScreenContract, ScreenType


@dataclass(frozen=True)
class RenderedTemplate:
    component: str
    stylesheet: str
    playwright_spec: str


@dataclass(frozen=True)
class TemplatePack:
    pack_id: str
    version: str
    screen_type: ScreenType
    renderer: Callable[[ScreenContract, str], RenderedTemplate]

    def render(self, contract: ScreenContract, component_name: str) -> RenderedTemplate:
        return self.renderer(contract, component_name)


def _js(value: str) -> str:
    return json.dumps(str(value))


def _criteria_comments(contract: ScreenContract) -> str:
    return "\n".join(
        f"// {item.criterion_id}: Given {item.given}, when {item.when}, then {item.then}."
        for item in contract.acceptance_criteria
    )


def _shared_spec(contract: ScreenContract, component_name: str) -> str:
    checks = []
    for item in contract.acceptance_criteria:
        checks.append(
            "test(%s, async ({ page }) => {\n"
            "  await page.goto(%s);\n"
            "  await expect(page.locator('[data-screen-id=%s]')).toBeVisible();\n"
            "  // Verification: %s\n"
            "});"
            % (
                _js(f"{item.criterion_id}: {item.then}"),
                _js(contract.route),
                _js(contract.screen_id),
                item.verification.replace("\n", " "),
            )
        )
    return (
        "import { expect, test } from '@playwright/test';\n\n"
        f"test.describe({_js(component_name)}, () => {{\n"
        + "\n\n".join("  " + block.replace("\n", "\n  ") for block in checks)
        + "\n});\n"
    )


def _shared_styles(contract: ScreenContract) -> str:
    themes = set(contract.theme_modes)
    dark = "" if "dark" not in themes else """
[data-theme='dark'] .ui-screen {
  --ui-surface: #111827;
  --ui-surface-raised: #1f2937;
  --ui-text: #f9fafb;
  --ui-muted: #cbd5e1;
  --ui-border: #475569;
  --ui-accent: #f59e0b;
  --ui-accent-text: #111827;
  --ui-danger: #f87171;
  --ui-focus: #fbbf24;
}
"""
    return f"""/* Generated from {contract.screen_id} contract {contract.fingerprint}. */
.ui-screen {{
  --ui-surface: #ffffff;
  --ui-surface-raised: #f8fafc;
  --ui-text: #111827;
  --ui-muted: #475569;
  --ui-border: #cbd5e1;
  --ui-accent: #1d4ed8;
  --ui-accent-text: #ffffff;
  --ui-danger: #b91c1c;
  --ui-focus: #2563eb;
  color: var(--ui-text);
  background: var(--ui-surface);
  min-height: 100%;
  padding: 24px;
}}

.ui-screen *, .ui-screen *::before, .ui-screen *::after {{ box-sizing: border-box; }}
.ui-screen :focus-visible {{ outline: 3px solid var(--ui-focus); outline-offset: 2px; }}
.ui-header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }}
.ui-title {{ margin: 0; font: 700 clamp(1.5rem, 3vw, 2rem)/1.15 system-ui, sans-serif; }}
.ui-primary, .ui-secondary {{ min-height: 44px; border-radius: 8px; padding: 10px 16px; font: 600 0.95rem/1 system-ui, sans-serif; }}
.ui-primary {{ border: 1px solid var(--ui-accent); background: var(--ui-accent); color: var(--ui-accent-text); }}
.ui-secondary {{ border: 1px solid var(--ui-border); background: var(--ui-surface); color: var(--ui-text); }}
.ui-panel {{ border: 1px solid var(--ui-border); border-radius: 10px; background: var(--ui-surface); }}
.ui-toolbar {{ display: flex; gap: 12px; align-items: end; padding: 16px; border-bottom: 1px solid var(--ui-border); }}
.ui-field {{ display: grid; gap: 6px; min-width: 0; }}
.ui-field label {{ color: var(--ui-muted); font: 600 0.8rem/1.2 system-ui, sans-serif; }}
.ui-field input, .ui-field select {{ min-height: 44px; border: 1px solid var(--ui-border); border-radius: 8px; background: var(--ui-surface); color: var(--ui-text); padding: 8px 10px; }}
.ui-table {{ width: 100%; border-collapse: collapse; }}
.ui-table th, .ui-table td {{ padding: 12px 16px; border-bottom: 1px solid var(--ui-border); text-align: left; }}
.ui-table th {{ color: var(--ui-muted); font-size: 0.8rem; }}
.ui-table td[data-numeric='true'], .ui-table th[data-numeric='true'] {{ text-align: right; font-variant-numeric: tabular-nums; }}
.ui-state {{ padding: 32px 20px; text-align: center; color: var(--ui-muted); }}
.ui-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
.ui-card {{ border: 1px solid var(--ui-border); border-radius: 10px; padding: 16px; background: var(--ui-surface-raised); }}
.ui-skeleton {{ min-height: 18px; border-radius: 6px; background: color-mix(in srgb, var(--ui-muted) 18%, transparent); }}
.ui-error {{ border-left: 4px solid var(--ui-danger); padding: 16px; color: var(--ui-text); }}
.ui-pagination {{ display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding: 16px; }}
{dark}
@media (max-width: 720px) {{
  .ui-screen {{ padding: 16px; }}
  .ui-header {{ align-items: stretch; flex-direction: column; }}
  .ui-primary {{ width: 100%; }}
  .ui-toolbar, .ui-grid {{ grid-template-columns: 1fr; flex-direction: column; align-items: stretch; }}
  .ui-table thead {{ display: none; }}
  .ui-table, .ui-table tbody, .ui-table tr, .ui-table td {{ display: block; width: 100%; }}
  .ui-table tr {{ padding: 12px; border-bottom: 1px solid var(--ui-border); }}
  .ui-table td {{ display: flex; justify-content: space-between; gap: 16px; border: 0; padding: 6px 0; }}
  .ui-table td::before {{ content: attr(data-label); color: var(--ui-muted); font-weight: 600; }}
}}
"""


def _state_switch(contract: ScreenContract, populated: str) -> str:
    return f"""  if (state === 'loading') return <div className="ui-state" aria-busy="true"><div className="ui-skeleton" /><span className="sr-only">Loading</span></div>;
  if (state === 'error') return <div className="ui-error" role="alert"><p>Unable to load {contract.entity}.</p><button className="ui-secondary" type="button" onClick={{onRetry}}>Retry</button></div>;
  if (state === 'empty') return <div className="ui-state"><h2>No {contract.entity} yet</h2><p>Use {contract.primary_action.lower()} to add the first {contract.entity}.</p></div>;
{populated}"""


def _render_crud(contract: ScreenContract, component_name: str) -> RenderedTemplate:
    headers = "\n".join(f"          <th scope=\"col\">{column.replace('_', ' ').title()}</th>" for column in contract.columns)
    cells = "\n".join(
        f"            <td data-label={_js(column.replace('_', ' ').title())}>{{String(row[{_js(column)}] ?? '')}}</td>"
        for column in contract.columns
    )
    filters = "\n".join(
        f"        <div className=\"ui-field\"><label htmlFor={_js(f'{contract.screen_id}-{name}')}>{name.replace('_', ' ').title()}</label><select id={_js(f'{contract.screen_id}-{name}')}><option>All</option></select></div>"
        for name in contract.filters
    )
    populated = f"""  return <div className="ui-panel">
    <div className="ui-toolbar"><div className="ui-field"><label htmlFor="{contract.screen_id}-search">Search</label><input id="{contract.screen_id}-search" type="search" /></div>{filters}</div>
    <table className="ui-table"><thead><tr>{headers}</tr></thead><tbody>{{rows.map((row, index) => <tr key={{String(row.id ?? index)}}>{cells}</tr>)}}</tbody></table>
    <nav className="ui-pagination" aria-label="Pagination"><button className="ui-secondary" type="button" disabled={{page <= 1}} onClick={{onPreviousPage}}>Previous</button><span>Page {{page}} of {{pageCount}}</span><button className="ui-secondary" type="button" disabled={{page >= pageCount}} onClick={{onNextPage}}>Next</button></nav>
  </div>;"""
    component = f"""import './{component_name}.css';

{_criteria_comments(contract)}
export type {component_name}State = 'loading' | 'empty' | 'error' | 'populated';
export interface {component_name}Props {{ state: {component_name}State; rows: Array<Record<string, unknown>>; page: number; pageCount: number; onPrimaryAction(): void; onRetry(): void; onPreviousPage(): void; onNextPage(): void; }}

export function {component_name}({{ state, rows, page, pageCount, onPrimaryAction, onRetry, onPreviousPage, onNextPage }}: {component_name}Props) {{
  const content = (() => {{
{_state_switch(contract, populated)}
  }})();
  return <main className="ui-screen" data-screen-id={_js(contract.screen_id)}><header className="ui-header"><h1 className="ui-title">{contract.title}</h1><button className="ui-primary" type="button" onClick={{onPrimaryAction}}>{contract.primary_action}</button></header>{{content}}</main>;
}}
"""
    return RenderedTemplate(component, _shared_styles(contract), _shared_spec(contract, component_name))


def _render_admin(contract: ScreenContract, component_name: str) -> RenderedTemplate:
    populated = "  return <section className=\"ui-panel\" aria-label=\"Content\">{children}</section>;"
    component = f"""import type {{ ReactNode }} from 'react';
import './{component_name}.css';

{_criteria_comments(contract)}
export interface {component_name}Props {{ state: 'loading' | 'empty' | 'error' | 'populated'; children: ReactNode; onPrimaryAction(): void; onRetry(): void; }}
export function {component_name}({{ state, children, onPrimaryAction, onRetry }}: {component_name}Props) {{
  const content = (() => {{
{_state_switch(contract, populated)}
  }})();
  return <main className="ui-screen" data-screen-id={_js(contract.screen_id)}><header className="ui-header"><h1 className="ui-title">{contract.title}</h1><button className="ui-primary" type="button" onClick={{onPrimaryAction}}>{contract.primary_action}</button></header>{{content}}</main>;
}}
"""
    return RenderedTemplate(component, _shared_styles(contract), _shared_spec(contract, component_name))


def _render_portal(contract: ScreenContract, component_name: str) -> RenderedTemplate:
    component = f"""import type {{ ReactNode }} from 'react';
import './{component_name}.css';

{_criteria_comments(contract)}
export interface {component_name}Item {{ id: string; name: string; description: string; price: string; imageUrl: string; }}
export interface {component_name}Props {{ state: 'loading' | 'empty' | 'error' | 'populated'; items: {component_name}Item[]; onPrimaryAction(): void; onRetry(): void; }}
export function {component_name}({{ state, items, onPrimaryAction, onRetry }}: {component_name}Props) {{
  let content: ReactNode;
  if (state === 'loading') content = <div className="ui-grid" aria-busy="true"><div className="ui-card ui-skeleton" /><div className="ui-card ui-skeleton" /></div>;
  else if (state === 'error') content = <div className="ui-error" role="alert"><p>Unable to load {contract.entity}.</p><button className="ui-secondary" onClick={{onRetry}}>Retry</button></div>;
  else if (state === 'empty') content = <div className="ui-state"><h2>No {contract.entity} available</h2><p>Check back when new {contract.entity} have been added.</p></div>;
  else content = <div className="ui-grid">{{items.map(item => <article className="ui-card" key={{item.id}}><img src={{item.imageUrl}} alt="" /><h2>{{item.name}}</h2><p>{{item.description}}</p><p data-numeric="true">{{item.price}}</p></article>)}}</div>;
  return <main className="ui-screen" data-screen-id={_js(contract.screen_id)}><header className="ui-header"><h1 className="ui-title">{contract.title}</h1><button className="ui-primary" type="button" onClick={{onPrimaryAction}}>{contract.primary_action}</button></header>{{content}}</main>;
}}
"""
    return RenderedTemplate(component, _shared_styles(contract), _shared_spec(contract, component_name))


def _render_dashboard(contract: ScreenContract, component_name: str) -> RenderedTemplate:
    populated = "  return <section className=\"ui-grid\" aria-label=\"Requested measures\">{measures.map(measure => <article className=\"ui-card\" key={measure.id}><h2>{measure.label}</h2><strong>{measure.value}</strong><p>{measure.timeframe} · {measure.freshness}</p></article>)}</section>;"
    component = f"""import './{component_name}.css';

{_criteria_comments(contract)}
export interface {component_name}Measure {{ id: string; label: string; value: string; timeframe: string; freshness: string; }}
export function {component_name}({{ state, measures, onPrimaryAction, onRetry }}: {{ state: 'loading' | 'empty' | 'error' | 'populated'; measures: {component_name}Measure[]; onPrimaryAction(): void; onRetry(): void }}) {{
  const content = (() => {{
{_state_switch(contract, populated)}
  }})();
  return <main className="ui-screen" data-screen-id={_js(contract.screen_id)}><header className="ui-header"><h1 className="ui-title">{contract.title}</h1><button className="ui-primary" type="button" onClick={{onPrimaryAction}}>{contract.primary_action}</button></header>{{content}}</main>;
}}
"""
    return RenderedTemplate(component, _shared_styles(contract), _shared_spec(contract, component_name))


def _render_funnel(contract: ScreenContract, component_name: str) -> RenderedTemplate:
    fields = "\n".join(
        f"<div className=\"ui-field\"><label htmlFor={_js(f'{contract.screen_id}-{item.name}')}>{item.label}</label><input id={_js(f'{contract.screen_id}-{item.name}')} name={_js(item.name)} type={_js(item.field_type)} {'required' if item.required else ''} /></div>"
        for item in contract.fields
    )
    component = f"""import './{component_name}.css';

{_criteria_comments(contract)}
export function {component_name}({{ state, onSubmit, onRetry }}: {{ state: 'loading' | 'error' | 'populated'; onSubmit(data: FormData): void; onRetry(): void }}) {{
  let content;
  if (state === 'loading') content = <div className="ui-state" aria-busy="true"><div className="ui-skeleton" /><span className="sr-only">Loading</span></div>;
  else if (state === 'error') content = <div className="ui-error" role="alert"><p>Unable to load this step.</p><button className="ui-secondary" type="button" onClick={{onRetry}}>Retry</button></div>;
  else content = <form className="ui-panel ui-card" onSubmit={{event => {{ event.preventDefault(); onSubmit(new FormData(event.currentTarget)); }}}}>{fields}<div className="ui-header"><button className="ui-secondary" type="button">Back</button><button className="ui-primary" type="submit">{contract.primary_action}</button></div></form>;
  return <main className="ui-screen" data-screen-id={_js(contract.screen_id)}><header className="ui-header"><h1 className="ui-title">{contract.title}</h1></header>{{content}}</main>;
}}
"""
    return RenderedTemplate(component, _shared_styles(contract), _shared_spec(contract, component_name))


PACKS = {
    ScreenType.ADMIN: TemplatePack("admin-screen", "1.0.0", ScreenType.ADMIN, _render_admin),
    ScreenType.CRUD: TemplatePack("crud-list-detail", "1.0.0", ScreenType.CRUD, _render_crud),
    ScreenType.CUSTOMER_PORTAL: TemplatePack("customer-portal", "1.0.0", ScreenType.CUSTOMER_PORTAL, _render_portal),
    ScreenType.DASHBOARD: TemplatePack("dashboard", "1.0.0", ScreenType.DASHBOARD, _render_dashboard),
    ScreenType.FUNNEL: TemplatePack("funnel", "1.0.0", ScreenType.FUNNEL, _render_funnel),
}


def get_template_pack(screen_type: ScreenType) -> TemplatePack:
    try:
        return PACKS[screen_type]
    except KeyError as exc:
        raise ValueError(f"No template pack registered for {screen_type.value}") from exc
