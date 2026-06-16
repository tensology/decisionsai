# Spotify-style web player remake (E2E requirements)

## Product goal

Build a polished Spotify-style music web app with sample data only (no auth, no backend).
The app must feel production-ready on desktop and mobile.

## Required views

1. App shell with sidebar navigation and a persistent bottom player.
2. Library view with playlists and liked songs.
3. Search view with query filtering and empty states.
4. Browse view with albums, artists, and cards.

## Interactions

- Play/pause, skip, queue tracks, like/unlike, and basic playlist actions.
- State updates must be visible without page reload.
- Keyboard and pointer accessible controls.

## Quality bar

- No console errors on primary flows.
- Responsive layout from mobile to desktop.
- No hardcoded secrets, unsafe eval, or dead placeholder screens.
- Lint/test/build commands from the project config must pass before ship.

## Delivery slices (tickets)

The ideation workflow must create separate board tickets for:

1. App shell and player foundation.
2. Library, search, and browse views.
3. Queue, playlist actions, and liked tracks.

Polish and security verification run as a separate workflow after development tickets complete.
