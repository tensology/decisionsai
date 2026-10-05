// Merrypak page cards only. A missing set keeps the stencil for every other project.
// notFound marks a saved capture of the site's own not-found screen. Those stay in their access group, but not as the large cards.
export const pageCaptureSets = [
  {
    "workspaceId": 4,
    "projectId": 2,
    "rootPrefix": "/Users/paul/development/WORK/CRYSTALLOGIC/www.merrypak.co.za",
    "baseUrl": "https://www.merrypak.co.za",
    "pages": {
      "page-register": {
        "image": "/static/development/planning/captures/merrypak/page-register.png"
      },
      "page-activate": {
        "image": null,
        "reason": "left the page URL; ended on https://www.merrypak.co.za/activate/failed"
      },
      "page-resend-activation": {
        "image": "/static/development/planning/captures/merrypak/page-resend-activation.png",
        "notFound": true
      },
      "page-profile": {
        "image": "/static/development/planning/captures/merrypak/page-profile.png",
        "notFound": true
      },
      "page-profile-lookup": {
        "image": "/static/development/planning/captures/merrypak/page-profile-lookup.png",
        "notFound": true
      },
      "page-reset-password": {
        "image": "/static/development/planning/captures/merrypak/page-reset-password.png"
      },
      "page-forgot-password": {
        "image": "/static/development/planning/captures/merrypak/page-forgot-password.png"
      },
      "page-change-password": {
        "image": "/static/development/planning/captures/merrypak/page-change-password.png",
        "notFound": true
      },
      "page-change-email": {
        "image": "/static/development/planning/captures/merrypak/page-change-email.png",
        "notFound": true
      },
      "page-confirm-email": {
        "image": "/static/development/planning/captures/merrypak/page-confirm-email.png",
        "notFound": true
      },
      "page-valuecard-register": {
        "image": "/static/development/planning/captures/merrypak/page-valuecard-register.png",
        "notFound": true
      },
      "page-submit-temporary-perk-card": {
        "image": "/static/development/planning/captures/merrypak/page-submit-temporary-perk-card.png",
        "notFound": true
      },
      "page-perk-card-lookup": {
        "image": "/static/development/planning/captures/merrypak/page-perk-card-lookup.png",
        "notFound": true
      },
      "page-perk-card-receiver": {
        "image": "/static/development/planning/captures/merrypak/page-perk-card-receiver.png",
        "notFound": true
      },
      "page-session": {
        "image": "/static/development/planning/captures/merrypak/page-session.png",
        "notFound": true
      },
      "page-logout": {
        "image": null,
        "reason": "redirected to login (https://www.merrypak.co.za/login)"
      },
      "page-validate-reset-link": {
        "image": "/static/development/planning/captures/merrypak/page-validate-reset-link.png",
        "notFound": true
      },
      "page-validate-password": {
        "image": "/static/development/planning/captures/merrypak/page-validate-password.png",
        "notFound": true
      },
      "page-confirm-reset-password": {
        "image": "/static/development/planning/captures/merrypak/page-confirm-reset-password.png",
        "notFound": true
      },
      "page-provinces": {
        "image": "/static/development/planning/captures/merrypak/page-provinces.png",
        "notFound": true
      },
      "page-cities": {
        "image": "/static/development/planning/captures/merrypak/page-cities.png",
        "notFound": true
      },
      "page-suburbs": {
        "image": "/static/development/planning/captures/merrypak/page-suburbs.png",
        "notFound": true
      },
      "page-location-search": {
        "image": "/static/development/planning/captures/merrypak/page-location-search.png",
        "notFound": true
      },
      "page-catalogue-upload": {
        "image": "/static/development/planning/captures/merrypak/page-catalogue-upload.png",
        "notFound": true
      },
      "page-upload-files": {
        "image": "/static/development/planning/captures/merrypak/page-upload-files.png",
        "notFound": true
      },
      "page-delete-files": {
        "image": "/static/development/planning/captures/merrypak/page-delete-files.png",
        "notFound": true
      },
      "page-analyze-files": {
        "image": "/static/development/planning/captures/merrypak/page-analyze-files.png",
        "notFound": true
      },
      "page-catalogue-preflight": {
        "image": "/static/development/planning/captures/merrypak/page-catalogue-preflight.png"
      },
      "page-list": {
        "image": "/static/development/planning/captures/merrypak/page-list.png",
        "notFound": true
      },
      "page-detail": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-admin-send-again": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-login-required": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-login-required-2": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-email-send-test": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-home": {
        "image": "/static/development/planning/captures/merrypak/page-home.png"
      },
      "page-robots-txt": {
        "image": "/static/development/planning/captures/merrypak/page-robots-txt.png"
      },
      "page-versioned-cache-sitemap": {
        "image": "/static/development/planning/captures/merrypak/page-versioned-cache-sitemap.png"
      },
      "page-versioned-cache-sitemap-2": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "page-serve-nocache-media": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "missing-admin-index": {
        "image": null,
        "reason": "no concrete GET url (empty route or path parameter)"
      },
      "missing-admin-commands": {
        "image": null,
        "reason": "redirected to login (https://www.merrypak.co.za/admin/login/?next=/admin/commands)"
      }
    }
  }
];

export function pageCaptureFor(workspace) {
    const root = String(workspace?.root_path || '').replace(/\/+$/, '');
    const workspaceId = Number(workspace?.id);
    const projectId = Number(workspace?.project_id);
    return pageCaptureSets.find((set) => {
        if (root === set.rootPrefix || root.startsWith(set.rootPrefix + '/')) return true;
        if (workspaceId === set.workspaceId) return true;
        return projectId === set.projectId && root.includes('merrypak');
    }) || null;
}
