// Relation path + crow's-foot end markers (pure; used by canvas.js and tests).
import { TABLE_WIDTH, tableHeight } from './layout.js?v=20261102-verify';
import { endKind } from './parser.js?v=20261103-ifaces3';

const box = (t) => ({ x: t.x, y: t.y, w: TABLE_WIDTH, h: tableHeight(t) });

export function relationGeometry(a, b) {
    const A = box(a), B = box(b);
    const acx = A.x + A.w / 2, bcx = B.x + B.w / 2, acy = A.y + A.h / 2, bcy = B.y + B.h / 2;
    let p1, p2, d1, d2;
    if (a === b) {  // self relation: loop on the right side
        p1 = [A.x + A.w, A.y + 14]; p2 = [A.x + A.w, A.y + 40]; d1 = [1, 0]; d2 = [1, 0];
        return { p1, p2, d1, d2, path: `M${p1} C${p1[0] + 60},${p1[1]} ${p2[0] + 60},${p2[1]} ${p2}` };
    }
    if (B.x > A.x + A.w + 20) { p1 = [A.x + A.w, acy]; p2 = [B.x, bcy]; d1 = [1, 0]; d2 = [-1, 0]; }
    else if (A.x > B.x + B.w + 20) { p1 = [A.x, acy]; p2 = [B.x + B.w, bcy]; d1 = [-1, 0]; d2 = [1, 0]; }
    else if (bcy >= acy) { p1 = [acx, A.y + A.h]; p2 = [bcx, B.y]; d1 = [0, 1]; d2 = [0, -1]; }
    else { p1 = [acx, A.y]; p2 = [bcx, B.y + B.h]; d1 = [0, -1]; d2 = [0, 1]; }
    const k = Math.max(40, Math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 3);
    const c1 = [p1[0] + d1[0] * k, p1[1] + d1[1] * k], c2 = [p2[0] + d2[0] * k, p2[1] + d2[1] * k];
    return { p1, p2, d1, d2, path: `M${p1} C${c1} ${c2} ${p2}` };
}

// Marker in local coords: +x points away from the table along the line.
export function markerPath(token) {
    const { min, many } = endKind(token);
    const parts = [];
    if (many) parts.push('M0,-7 L12,0 M0,7 L12,0 M0,0 L12,0'); else parts.push('M8,-7 L8,7');
    if (min === 1) parts.push('M18,-7 L18,7');
    return { d: parts.join(' '), circle: min === 0 ? { cx: 22, cy: 0, r: 4 } : null };
}

export const markerTransform = (p, d) => `translate(${p[0]},${p[1]}) rotate(${Math.atan2(d[1], d[0]) * 180 / Math.PI})`;
