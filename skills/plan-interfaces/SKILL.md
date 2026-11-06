---
name: plan-interfaces
description: Use when rebuilding or editing the Plan Interfaces sitemap, page outlines, or how screens connect. Use this whenever a plan rebuild, sitemap, page flow, or interface canvas is being written, including the Rebuild button on Interfaces.
---

# Plan interfaces

The Interfaces canvas is a map of pages. A card is a page. A line is a flow you named. The layout leaves a gap between connected pages so the line sits in open space.

## What to write

Return one wire document.

- One `screen "Name" id=page-id route=/path layer=frontend` per page that already exists. `layer` is `backend`, `frontend`, `ajax`, or `guidance`.
- Backend is a server page or admin tool. Frontend is a page a person opens. Ajax is a call the page makes (`/api/`, `/ajax/`, or a JSON route), not a second copy of the page. Guidance is a short note about how something works. It is not a page, so it has no route.
- Inside a screen, write `link "Label" route=/next` for each real next step (the primary forward step, back, success, or failure). The route must already exist. Do not invent routes. A guidance note uses the same `link` to the page it explains.
- Copy those connections into the last-line sidecar `links` map, keyed by the target screen id: `"links":{"next-id":"Label"}`.
- A page may carry one short comment. Put it in the sidecar `note`, or as the first `#|` line directly under that screen. Guidance nodes are the comment: the sidecar `note` is the guidance.
- Keep screen ids, `#|` descriptions, and the sidecar. Do not set x or y. The canvas puts each layer in its own band and leaves a gap where a line connects two cards.

## How the map should read

- Backend, Frontend, Ajax, and Guidance are separate bands. Do not mix them into one grid.
- A line may cross from one band to another. That is the connection. Leave the pair readable: the entry page comes before the page it opens.
- A connected pair in the same band is two cards with a clear gap and one line between them.
- A page with several exits names each exit. Each target gets its own gap.
- Pages that do not connect stay apart from those pairs.

## Page images

Screenshots live in the linked project's `/.planning/` folder. The file name is the screen id plus `.png`, `.jpg`, or `.webp` (for example `customers.png`). `planning_images` lists the filenames already in that folder. Match a page to a file by screen id. Do not invent a URL. Do not put a filename in the sidecar `image` field. That field is only an existing attachment id.
