# Decisions Wireframe DSL

The first visual planning slice uses a small, indented, semantic language. The source is the durable artifact. SVG is a deterministic preview, not the source of truth.

```wire
screen "Sign in" device=desktop
  nav "Account"
    link "Overview"
    link "Settings" active=true
  stack gap=16
    heading "Welcome back"
    text "Sign in to continue"
    form "Credentials"
      field "Email" type=email bind=user.email
      field "Password" type=password bind=user.password
      row gap=10
        checkbox "Remember me"
        button "Sign in" variant=primary action=submit
```

Rules:

- Two-space indentation creates the layout tree.
- Components describe intent, not CSS or pixel coordinates.
- Labels are quoted when they contain spaces.
- Attributes carry interaction, data binding, and responsive intent.
- Unknown components become diagnostics and do not execute arbitrary HTML.

The initial vocabulary covers screens, navigation, layout containers, forms, fields, selects, calendar/date controls, buttons, tabs, cards, lists, tables, drag lists, status, and common content controls. New components should be added to the AST and renderer as a vertical slice, then mapped to Tailwind or the target application stack in a later compiler phase.

## Phased build

1. **Wireframe source and SVG preview:** parse, validate, render, edit, save, and live-preview one wireframe item.
2. **Planning graph:** add stable screen, component, requirement, entity, and task references; connect sitemap, PRD, FRAC, ERD, and wireframes.
3. **Agent planning contract:** assemble the selected plan revision and linked graph into the planning agent context; require proposal, diff, and approval before writes.
4. **HTML/Tailwind compiler:** compile the same AST to semantic HTML and a target adapter, starting with Tailwind utility classes and then project stack adapters.
5. **Delivery decomposition:** derive page, model, view, template, API, and test tickets from approved screens and FR/AC links.
6. **Plan E2E harness:** preview every screen, exercise declared interactions, compare SVG/HTML snapshots, and report missing traceability or broken states before tickets are created.
