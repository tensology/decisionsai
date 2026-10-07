# -*- coding: utf-8 -*-
"""Batch-add ~46 i18n keys (theme popover + Why lists + Tier lists) to all 6 langs.
   Run once. Deletes itself? No — keep for re-runs if needed.
   Glossary lock: technical terms in <code> tags stay EN; connector words translate."""

import json, os, sys
from collections import OrderedDict

os.chdir(os.path.join(os.path.dirname(__file__), '..'))  # cwd → site/

DATA = {
    'en': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': 'designed via perfect-ui',
            'footer_desc': 'Each theme = one vibe anchor from <code>visual-direction-guide.md</code> — palette + typography + motion ease + custom P-logo. DOM structure stays Editorial; example pages remain Editorial-only.',
            'cta_generate': 'Generate custom via perfect-ui →',
        },
        'why_ai_items': [
            'Inter font alone',
            'Purple/blue gradient hero',
            '<code>h-screen</code> everywhere',
            '<code>lucide-react</code> icons',
            '3-col equal feature grid',
            '"Get Started" CTAs',
            'Round fake stats (10K+, 99.99%)',
            'Fade-up on every element',
        ],
        'why_human_items': [
            'One vibe locked end-to-end',
            'Custom SVG icon set',
            'Asymmetric editorial grid',
            'Vibe-paired typography',
            'Distinctive display fonts',
            'Specific draft copy',
            'Real evidence-based stats',
            'Vibe-scaled motion intensity',
        ],
        'tier1_items': [
            '<code>DM Sans + Space Grotesk</code> pair',
            'Purple/blue gradient hero with gradient text',
            '<code>lucide-react</code> / <code>@heroicons</code> imports',
            'Two equal-weight CTAs in hero',
            '<code>h-screen</code> utility class',
            '3-column equal feature card grid',
            'Tailwind density &gt; 200 utilities',
            'Generic CTAs ("Get Started", "Sign In")',
            'AI-generated 3D model as hero subject',
            '<code>OrbitControls</code> in landing/portfolio',
        ],
        'tier2_items': [
            'Generic browser-mockup right-half of split hero',
            'Friendly bullet checkbox reassurance row',
            'Round fake stats (10K+, 99.99%)',
            'AI cute illustration as hero',
            'Centered hero + centered H1 at high variance',
            '3D model used purely as decoration',
            'Style mixing across 2D illustrations',
            'Generic fade-up motion on every element',
            '<code>ease-in-out</code> 0.3s as universal duration',
        ],
        'tier3_items': [
            '<code>Inter</code> body when paired with distinctive display',
            'AI cliché phrases in non-load-bearing copy',
            'Generic startup names (Acme, Globex)',
            'Title Case On Every Header',
        ],
    },
    'vi': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': 'thiết kế bởi perfect-ui',
            'footer_desc': 'Mỗi theme = một vibe anchor từ <code>visual-direction-guide.md</code> — palette + typography + motion ease + P-logo custom. Cấu trúc DOM giữ Editorial; trang ví dụ vẫn Editorial-only.',
            'cta_generate': 'Tạo custom qua perfect-ui →',
        },
        'why_ai_items': [
            'Chỉ Inter',
            'Hero gradient tím/xanh',
            '<code>h-screen</code> khắp nơi',
            'Icon <code>lucide-react</code>',
            'Grid 3 cột feature đều nhau',
            'CTA "Get Started"',
            'Stat tròn giả (10K+, 99.99%)',
            'Fade-up trên mọi element',
        ],
        'why_human_items': [
            'Một vibe khoá end-to-end',
            'Bộ icon SVG custom',
            'Grid editorial bất đối xứng',
            'Typography ghép vibe',
            'Font display đặc trưng',
            'Copy draft cụ thể',
            'Stat thực dựa trên evidence',
            'Motion intensity scale theo vibe',
        ],
        'tier1_items': [
            'Cặp <code>DM Sans + Space Grotesk</code>',
            'Hero gradient tím/xanh + text gradient',
            'Import <code>lucide-react</code> / <code>@heroicons</code>',
            'Hai CTA cùng trọng số ở hero',
            'Utility class <code>h-screen</code>',
            'Grid 3 cột card feature đều nhau',
            'Tailwind density &gt; 200 utility',
            'CTA generic ("Get Started", "Sign In")',
            'Model 3D AI sinh làm hero subject',
            '<code>OrbitControls</code> trong landing/portfolio',
        ],
        'tier2_items': [
            'Browser-mockup generic ở nửa phải split hero',
            'Hàng checkbox bullet "thân thiện"',
            'Stat tròn giả (10K+, 99.99%)',
            'Illustration cute AI sinh làm hero',
            'Hero center + H1 center ở high variance',
            'Model 3D dùng thuần như decoration',
            'Trộn style giữa các illustration 2D',
            'Fade-up motion generic trên mọi element',
            '<code>ease-in-out</code> 0.3s làm duration phổ quát',
        ],
        'tier3_items': [
            '<code>Inter</code> body khi ghép với display đặc trưng',
            'Cụm cliché AI trong copy không tải trọng',
            'Tên startup generic (Acme, Globex)',
            'Title Case Trên Mọi Header',
        ],
    },
    'zh': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': '由 perfect-ui 设计',
            'footer_desc': '每个 theme = 来自 <code>visual-direction-guide.md</code> 的一个 vibe anchor — palette + typography + motion ease + 自定义 P-logo。DOM 结构保持 Editorial；示例页面仅 Editorial。',
            'cta_generate': '通过 perfect-ui 生成自定义 →',
        },
        'why_ai_items': [
            '单独 Inter',
            '紫/蓝渐变 hero',
            '<code>h-screen</code> 到处用',
            '<code>lucide-react</code> 图标',
            '3 列等宽 feature grid',
            '"Get Started" CTA',
            '圆整假 stat (10K+, 99.99%)',
            '每个 element 都 fade-up',
        ],
        'why_human_items': [
            '单一 vibe 锁端到端',
            '自定义 SVG 图标集',
            '非对称 editorial grid',
            'Vibe 搭配的 typography',
            '独特的 display 字体',
            '具体的 draft copy',
            '基于真实证据的 stat',
            'Vibe 缩放的 motion intensity',
        ],
        'tier1_items': [
            '<code>DM Sans + Space Grotesk</code> 配对',
            '紫/蓝渐变 hero + 渐变文字',
            '<code>lucide-react</code> / <code>@heroicons</code> 导入',
            'Hero 中两个等权 CTA',
            '<code>h-screen</code> utility class',
            '3 列等宽 feature card grid',
            'Tailwind 密度 &gt; 200 utility',
            '通用 CTA ("Get Started", "Sign In")',
            'AI 生成的 3D 模型作 hero subject',
            'Landing/portfolio 中 <code>OrbitControls</code>',
        ],
        'tier2_items': [
            '分屏 hero 右半通用 browser-mockup',
            '友好 bullet checkbox 安抚行',
            '圆整假 stat (10K+, 99.99%)',
            'AI 可爱 illustration 作 hero',
            '高 variance 的居中 hero + 居中 H1',
            '3D 模型纯作装饰',
            '2D illustration 间混风格',
            '每个 element 通用 fade-up motion',
            '<code>ease-in-out</code> 0.3s 作通用 duration',
        ],
        'tier3_items': [
            '与独特 display 配对时的 <code>Inter</code> body',
            '非承重 copy 中的 AI 陈词',
            '通用 startup 名 (Acme, Globex)',
            '每个 Header 都 Title Case',
        ],
    },
    'ko': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': 'perfect-ui로 디자인',
            'footer_desc': '각 theme = <code>visual-direction-guide.md</code>의 vibe anchor 하나 — palette + typography + motion ease + 커스텀 P-로고. DOM 구조는 Editorial 유지; 예제 페이지는 Editorial 전용.',
            'cta_generate': 'perfect-ui로 커스텀 생성 →',
        },
        'why_ai_items': [
            'Inter 단독',
            '보라/파랑 그라데이션 hero',
            '<code>h-screen</code> 어디에나',
            '<code>lucide-react</code> 아이콘',
            '3열 동일 feature grid',
            '"Get Started" CTA',
            '둥근 가짜 stat (10K+, 99.99%)',
            '모든 element에 fade-up',
        ],
        'why_human_items': [
            '한 vibe end-to-end 잠금',
            '커스텀 SVG 아이콘 세트',
            '비대칭 editorial grid',
            'Vibe 페어링 typography',
            '독특한 display 폰트',
            '구체적인 draft copy',
            '실제 evidence 기반 stat',
            'Vibe 스케일 motion intensity',
        ],
        'tier1_items': [
            '<code>DM Sans + Space Grotesk</code> 페어',
            '보라/파랑 그라데이션 hero + 그라데이션 텍스트',
            '<code>lucide-react</code> / <code>@heroicons</code> import',
            'Hero에 동일 가중치 CTA 두 개',
            '<code>h-screen</code> utility 클래스',
            '3열 동일 feature card grid',
            'Tailwind 밀도 &gt; 200 utility',
            '일반 CTA ("Get Started", "Sign In")',
            'AI 생성 3D 모델을 hero subject로',
            'Landing/portfolio의 <code>OrbitControls</code>',
        ],
        'tier2_items': [
            '분할 hero 오른쪽 절반 일반 브라우저 mockup',
            '친근한 bullet checkbox 안심 행',
            '둥근 가짜 stat (10K+, 99.99%)',
            'Hero로 AI 귀여운 illustration',
            '높은 variance에 중앙 hero + 중앙 H1',
            '순수 장식용 3D 모델',
            '2D illustration 간 스타일 혼합',
            '모든 element 일반 fade-up motion',
            '범용 duration으로 <code>ease-in-out</code> 0.3s',
        ],
        'tier3_items': [
            '독특한 display와 페어링된 <code>Inter</code> body',
            '비중심 copy의 AI 클리셰 문구',
            '일반 startup 이름 (Acme, Globex)',
            '모든 Header에 Title Case',
        ],
    },
    'ja': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': 'perfect-ui によるデザイン',
            'footer_desc': '各 theme = <code>visual-direction-guide.md</code> からの 1 つの vibe anchor — palette + typography + motion ease + カスタム P-ロゴ。DOM 構造は Editorial のまま；例ページは Editorial 専用。',
            'cta_generate': 'perfect-ui でカスタム生成 →',
        },
        'why_ai_items': [
            'Inter 単独',
            '紫/青グラデーション hero',
            '<code>h-screen</code> どこでも',
            '<code>lucide-react</code> アイコン',
            '3 列均等 feature grid',
            '"Get Started" CTA',
            '丸い偽 stat (10K+, 99.99%)',
            '全要素 fade-up',
        ],
        'why_human_items': [
            '1 つの vibe を end-to-end ロック',
            'カスタム SVG アイコンセット',
            '非対称 editorial grid',
            'Vibe ペア typography',
            '特徴的な display フォント',
            '具体的な draft copy',
            'リアルな evidence ベース stat',
            'Vibe スケール motion intensity',
        ],
        'tier1_items': [
            '<code>DM Sans + Space Grotesk</code> ペア',
            '紫/青グラデーション hero + グラデーションテキスト',
            '<code>lucide-react</code> / <code>@heroicons</code> インポート',
            'Hero に等重 CTA 2 つ',
            '<code>h-screen</code> utility クラス',
            '3 列均等 feature カードグリッド',
            'Tailwind 密度 &gt; 200 utility',
            '汎用 CTA ("Get Started", "Sign In")',
            'AI 生成 3D モデルを hero subject に',
            'Landing/portfolio の <code>OrbitControls</code>',
        ],
        'tier2_items': [
            '分割 hero 右半分の汎用ブラウザ mockup',
            'フレンドリーな bullet チェックボックス安心行',
            '丸い偽 stat (10K+, 99.99%)',
            'AI のかわいいイラストを hero に',
            '高 variance での中央 hero + 中央 H1',
            '純粋装飾としての 3D モデル',
            '2D イラスト間でのスタイル混合',
            '全要素汎用 fade-up モーション',
            'ユニバーサル duration として <code>ease-in-out</code> 0.3s',
        ],
        'tier3_items': [
            '特徴的 display とペアの <code>Inter</code> body',
            '非荷重 copy 内の AI 決まり文句',
            '汎用 startup 名 (Acme, Globex)',
            '全ヘッダーで Title Case',
        ],
    },
    'es': {
        'theme_popover': {
            'header_label': 'Theme',
            'header_count': '11 vibes',
            'header_subtitle': 'diseñado vía perfect-ui',
            'footer_desc': 'Cada theme = un vibe anchor de <code>visual-direction-guide.md</code> — palette + typography + motion ease + P-logo personalizado. La estructura DOM se mantiene Editorial; las páginas de ejemplo siguen siendo solo Editorial.',
            'cta_generate': 'Generar custom vía perfect-ui →',
        },
        'why_ai_items': [
            'Inter sola',
            'Hero gradiente púrpura/azul',
            '<code>h-screen</code> en todas partes',
            'Iconos <code>lucide-react</code>',
            'Grid de 3 cols feature iguales',
            'CTAs "Get Started"',
            'Stats falsas redondas (10K+, 99.99%)',
            'Fade-up en cada elemento',
        ],
        'why_human_items': [
            'Un vibe bloqueado end-to-end',
            'Set de iconos SVG personalizados',
            'Grid editorial asimétrico',
            'Typography emparejada con vibe',
            'Fuentes display distintivas',
            'Copy draft específico',
            'Stats reales basadas en evidencia',
            'Motion intensity escalada por vibe',
        ],
        'tier1_items': [
            'Par <code>DM Sans + Space Grotesk</code>',
            'Hero gradiente púrpura/azul con texto gradiente',
            'Imports <code>lucide-react</code> / <code>@heroicons</code>',
            'Dos CTAs de igual peso en hero',
            'Clase utility <code>h-screen</code>',
            'Grid de 3 cols card feature iguales',
            'Densidad Tailwind &gt; 200 utilities',
            'CTAs genéricos ("Get Started", "Sign In")',
            'Modelo 3D generado por IA como hero subject',
            '<code>OrbitControls</code> en landing/portfolio',
        ],
        'tier2_items': [
            'Browser-mockup genérico en mitad derecha de hero dividido',
            'Fila de checkbox bullet amigable',
            'Stats falsas redondas (10K+, 99.99%)',
            'Ilustración AI cute como hero',
            'Hero centrado + H1 centrado con alta variance',
            'Modelo 3D usado puramente como decoración',
            'Mezcla de estilos entre ilustraciones 2D',
            'Motion fade-up genérico en cada elemento',
            '<code>ease-in-out</code> 0.3s como duración universal',
        ],
        'tier3_items': [
            '<code>Inter</code> body emparejado con display distintivo',
            'Frases cliché de IA en copy no portante',
            'Nombres genéricos de startup (Acme, Globex)',
            'Title Case En Cada Header',
        ],
    },
}

for lang, payload in DATA.items():
    path = f'i18n/{lang}.json'
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f, object_pairs_hook=OrderedDict)

    # Add 'theme' top-level block before 'tiers'
    new_d = OrderedDict()
    for k, v in d.items():
        if k == 'tiers':
            new_d['theme'] = payload['theme_popover']
        new_d[k] = v
    d = new_d

    # Add ai_items + human_items to 'why' block
    for i, item in enumerate(payload['why_ai_items'], 1):
        d['why'][f'ai_item_{i}'] = item
    for i, item in enumerate(payload['why_human_items'], 1):
        d['why'][f'human_item_{i}'] = item

    # Add tier items to 'tiers' block
    for i, item in enumerate(payload['tier1_items'], 1):
        d['tiers'][f't1_item_{i}'] = item
    for i, item in enumerate(payload['tier2_items'], 1):
        d['tiers'][f't2_item_{i}'] = item
    for i, item in enumerate(payload['tier3_items'], 1):
        d['tiers'][f't3_item_{i}'] = item

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f'  PASS  {lang}.json updated')

# Parity
def flat(d, p=''):
    out = {}
    for k, v in d.items():
        key = f'{p}.{k}' if p else k
        if isinstance(v, dict):
            out.update(flat(v, key))
        else:
            out[key] = v
    return out

print()
with open('i18n/en.json', encoding='utf-8') as f:
    en_keys = set(flat(json.load(f)).keys())
print(f'EN baseline: {len(en_keys)} keys')
for lang in ['vi', 'zh', 'ko', 'ja', 'es']:
    with open(f'i18n/{lang}.json', encoding='utf-8') as f:
        lk = set(flat(json.load(f)).keys())
    print(f'  {"PASS" if lk == en_keys else "FAIL"}  {lang}: {len(lk)} keys')

print()
print('Theme keys:', sum(1 for k in en_keys if k.startswith('theme.')))
print('Why item keys:', sum(1 for k in en_keys if 'why.ai_item_' in k or 'why.human_item_' in k))
print('Tier item keys:', sum(1 for k in en_keys if 'tiers.t1_item_' in k or 'tiers.t2_item_' in k or 'tiers.t3_item_' in k))
