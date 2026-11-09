from storage_paths import DATA_DIR
from api_errors import RequestBusyError
from llm_output_retry import output_retry
"""Inspiration mode: editable ingredient groups and diverse image batches."""
import model_registry
import json
import re
import secrets
import threading
import time
import uuid
import urllib.parse
from pathlib import Path
import exploration

DATA = DATA_DIR / 'inspiration_data'
LOCK = threading.RLock()
WORK = exploration.WORK

HTML = '''<section id="inspireView" class="view"><div class="explorePage">
<div class="exploreHead"><div><div class="editTitle">ひらめきモード</div><div class="editorHelp">材料を組み合わせたり、思いがけない発想を混ぜたりして、いろいろな画像を作ります。</div></div><div class="exploreActions" style="margin:0"><button id="inspireNew" class="secondary">＋ 新しいテーマ</button><button id="inspireDelete" class="dangerSmall" hidden>テーマを削除</button></div></div>
<label class="exploreLabel">保存したテーマ<select id="inspireSessions" class="select"><option value="">新しいテーマ</option></select></label>
<div id="inspireSetup"><label class="exploreLabel">テーマまたは最終プロンプト<textarea id="inspireTheme" rows="4" placeholder="画像のテーマ、または発想の元にしたい最終プロンプトを入力・貼り付け"></textarea></label><div class="sub">必ず守りたい条件はテーマに書いてください。モデル・サイズ・キャラクターは最初に選んだ設定を使います。キャラクター参照はQwen系モデル専用です。</div></div>
<div id="inspireSummary"></div>
<details id="inspireMaterials" class="inspireMaterialsFold" open hidden><summary>発想の材料</summary><div class="inspireSectionHead"><button id="inspireExpand" class="secondary">AIで材料を追加</button></div><div class="sub">グループや候補は自由に追加・削除できます。毎回すべてを使うわけではなく、新しい発想も混ざります。変更は自動保存します。</div><div id="inspireGroups"></div><div class="inspireAdd"><input id="inspireGroupName" placeholder="新しいグループ名（例：持ち物）"><button id="inspireAddGroup" class="secondary">グループを追加</button></div></details>
<div class="exploreSettings"><label>生成する画像数<div class="stepper" style="width:170px;margin-top:6px"><button id="inspireMinus" type="button">−</button><input id="inspireCount" type="number" min="1" step="1" value="12" required><button id="inspirePlus" type="button">＋</button></div></label><span class="sub">材料を変えながら、何度でも生成できます。</span></div>
<div class="exploreActions"><button id="inspireStart" class="primarySmall">テーマからひらめき</button><button id="inspireFromPrompt" class="directSmall">最終プロンプトからひらめき</button><button id="inspireResume" class="secondary" hidden>残りを生成する</button><button id="inspireStop" class="secondary" hidden>この1枚の完了後に中止</button></div>
<div id="inspireStatus" role="status" class="editorHelp"></div><div id="inspireBatches"></div>
</div></section>'''


def install_html(html):
    return html.replace('</head>', '<link rel="stylesheet" href="/inspiration.css"></head>', 1).replace(
        '</nav>', '<button class="tab" id="inspireTab">ひらめき</button></nav>', 1).replace(
        '</main>', HTML + '</main>', 1).replace('</body>', '<script src="/inspiration.js"></script></body>', 1)


def sid_path(sid):
    if not isinstance(sid, str) or not re.fullmatch(r'ins_[a-f0-9]{16}', sid):
        raise ValueError('テーマIDが不正です。')
    return DATA / (sid + '.json')


def load(sid):
    return json.loads(sid_path(sid).read_text(encoding='utf-8'))


def save(s):
    DATA.mkdir(exist_ok=True)
    s['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    path = sid_path(s['id'])
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def listing():
    out = []
    if DATA.exists():
        for path in DATA.glob('ins_*.json'):
            try:
                s = load(path.stem)
                out.append({k: s[k] for k in ('id', 'theme', 'updated_at')})
            except (ValueError, KeyError, OSError):
                continue
    return sorted(out, key=lambda s: s['updated_at'], reverse=True)


def count_value(value):
    if type(value) is not int or value < 1:
        raise ValueError('画像数は1以上の整数で指定してください。')
    return value


def norm(value):
    return str(value).strip().casefold()


def adopted_element_group(groups, element):
    value = norm(element['value'])
    candidates = [group for group in groups if any(norm(item) == value for item in group['values'])]
    return next((group for group in candidates if norm(group['name']) == norm(element['group'])),
                candidates[0] if len(candidates) == 1 else None)


def clean_groups(groups):
    if not isinstance(groups, list):
        raise ValueError('材料グループの形式が不正です。')
    result = []
    names = set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('材料グループの形式が不正です。')
        name = str(group.get('name', '')).strip()[:80]
        if not name or norm(name) in names:
            raise ValueError('グループ名は空欄にせず、重複しない名前にしてください。')
        values = group.get('values', [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('候補の形式が不正です。')
        unique = []
        for v in values:
            v = v.strip()[:200]
            if v and norm(v) not in {norm(x) for x in unique}:
                unique.append(v)
        names.add(norm(name))
        result.append({'name': name, 'values': unique})
    return result


def ai_json(bridge, system, data):
    return bridge['llm_json']([
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)},
    ], temperature=0.9)



def validate_ai_groups(result):
    groups = clean_groups(result.get('groups'))
    if not groups or not any(g['values'] for g in groups):
        raise ValueError('AIが材料を作成できませんでした。')
    return groups


GROUP_SYSTEM = '''画像の発想を広げるための材料を提案してください。テーマの明示的な条件は守り、テーマに合う日本語の材料グループを3〜5個、それぞれの候補を3〜5個作ります。
グループの分類自体をテーマに応じて考えてください。例えば持ち物、動作、場所、演出、物語の状況などですが、固定ではありません。
ありきたりな候補だけでなく、絵として魅力のある意外な候補を混ぜてください。
既存の材料と削除済みのグループ・候補が渡される場合、既存とは異なる候補を提案し、削除済みのものを復活させないでください。
base_promptがある場合は、元画像の完成プロンプトを材料に分解してください。元に含まれる要素と、そこから連想した別候補・新しい種類のグループを混ぜます。元の場面全体を固定条件として扱わず、削除済み材料を復活させないでください。
候補は短い日本語で、一つずつ独立して選べる表現にしてください。返答は次のJSONだけ。Markdownは禁止です。
{"groups":[{"name":"グループ名","values":["候補1","候補2","候補3"]}]}'''


def character_for(s, bridge):
    cid = s['settings']['character_id']
    character = bridge['get_character'](cid) if cid else None
    if cid and not character:
        raise ValueError('このテーマのキャラクターが見つかりません。')
    return character


def create(body, bridge):
    theme = str(body.get('theme', '')).strip()
    if not theme or len(theme) > 12000:
        raise ValueError('テーマを1〜12000文字で入力してください。')
    model = body.get('model', 'qwen21_turbo')
    model_registry.get(model)
    cid = str(body.get('character_id', ''))
    character = bridge['get_character'](cid) if cid else None
    if cid and not character:
        raise ValueError('キャラクターが見つかりません。')
    count = count_value(body.get('count', 12))
    base_prompt = theme if body.get('from_final_prompt') is True else ''
    groups = output_retry(lambda: ai_json(bridge, GROUP_SYSTEM, {'theme': '元のプロンプトを材料に多彩な画像へ発想を広げる' if base_prompt else theme, 'base_prompt': base_prompt, 'character_notes': character.get('notes', '') if character else ''}), validate_ai_groups, 'ひらめき・材料作成')
    s = {'id': 'ins_' + uuid.uuid4().hex[:16], 'theme': theme, 'base_prompt': base_prompt, 'groups': groups, 'removed_groups': [],
         'removed_values': [], 'settings': {'model': model, 'width': bridge['clamp_dimension'](body.get('width'), 1024),
         'height': bridge['clamp_dimension'](body.get('height'), 1024), 'character_id': cid,
         'character_name': character['name'] if character else '', 'count': count}, 'batches': []}
    if 'commit_llm_request' in bridge:
        bridge['commit_llm_request']()
    with LOCK:
        save(s)
    return {'session': s}


def refresh_pending_materials(s):
    if s['batches'] and s['batches'][-1]['status'] in ('pending', 'stopped'):
        batch = s['batches'][-1]
        batch['ideas'] = [i for i in batch['ideas'] if i['image']]
        batch['groups_used'] = json.loads(json.dumps(s['groups'], ensure_ascii=False))


def update_groups(s, groups):
    groups = clean_groups(groups)
    names = {norm(g['name']) for g in groups}
    values = {norm(v) for g in groups for v in g['values']}
    for old in s['groups']:
        if norm(old['name']) not in names and norm(old['name']) not in {norm(x) for x in s['removed_groups']}:
            s['removed_groups'].append(old['name'])
        for value in old['values']:
            if norm(value) not in values and norm(value) not in {norm(x) for x in s['removed_values']}:
                s['removed_values'].append(value)
    # Explicit human additions can restore something previously removed.
    s['removed_groups'] = [x for x in s['removed_groups'] if norm(x) not in names]
    s['removed_values'] = [x for x in s['removed_values'] if norm(x) not in values]
    s['groups'] = groups
    refresh_pending_materials(s)


def expand(s, bridge):
    context = {k: s[k] for k in ('theme', 'groups', 'removed_groups', 'removed_values')}
    context['base_prompt'] = s.get('base_prompt', '')
    if context['base_prompt']:
        context['theme'] = '元のプロンプトを材料に多彩な画像へ発想を広げる'
    additions = output_retry(lambda: ai_json(bridge, GROUP_SYSTEM, context), validate_ai_groups, 'ひらめき・材料追加')
    with LOCK:
        current = load(s['id'])
        if 'commit_llm_request' in bridge:
            bridge['commit_llm_request']()
        group_map = {norm(g['name']): g for g in current['groups']}
        for group in additions:
            if norm(group['name']) in {norm(x) for x in current['removed_groups']}:
                continue
            values = [v for v in group['values'] if norm(v) not in {norm(x) for x in current['removed_values']}]
            if not values:
                continue
            if norm(group['name']) not in group_map:
                new = {'name': group['name'], 'values': []}
                current['groups'].append(new)
                group_map[norm(group['name'])] = new
            target = group_map[norm(group['name'])]['values']
            for v in values:
                if norm(v) not in {norm(x) for x in target}:
                    target.append(v)
        refresh_pending_materials(current)
        save(current)
    return {'session': current}


def idea_for(s, bid, iid):
    for batch in s['batches']:
        if batch['id'] == bid:
            for idea in batch['ideas']:
                if idea['id'] == iid:
                    return batch, idea
    raise ValueError('画像が見つかりません。')


def image_path(image):
    name = image.get('filename', '')
    if not isinstance(name, str) or not re.fullmatch('[a-f0-9]{32}\\.png', name):
        raise ValueError('画像ファイル名が不正です。')
    file = DATA / name
    if file.resolve().parent != DATA.resolve():
        raise ValueError('画像の保存先が不正です。')
    return file


def plan_chunk(s, batch, bridge):
    remaining = batch['target_count'] - len(batch['ideas'])
    amount = min(8, remaining)
    groups = [g for g in s['groups'] if g['values']]
    recipes = []
    modes = ['組み合わせ', '組み合わせ', '連想を広げる', '自由な発想']
    for i in range(amount):
        mode = modes[(len(batch['ideas']) + i) % len(modes)]
        selected = []
        if groups and mode != '自由な発想':
            n = 1 if mode == '連想を広げる' or len(groups) == 1 else 2 + secrets.randbelow(min(3, len(groups)) - 1)
            for group in secrets.SystemRandom().sample(groups, n):
                selected.append({'group': group['name'], 'value': secrets.choice(group['values'])})
        recipes.append({'direction': mode, 'ingredients': selected})
    char = character_for(s, bridge)
    system = '''あなたは多彩で意外性のある画像の発想を考えるアシスタントです。テーマの明示的な指定は必ず守ってください。
テーマに明示された条件は、すべての最終英語プロンプトに省略せず明記してください。性別・年齢層・人数・髪色・外見・服装・画風など、指定のある項目を曖昧な言葉に置き換えないでください。
発想を広げるのはテーマで指定されていない部分です。材料の組み合わせ・連想・自由な発想のどの場合でも、テーマの明示条件を変更・省略しないでください。各案を出力する前に、すべての明示条件がその案の英語プロンプトに含まれていることを確認してください。
各recipeに対して、独立した完成英語プロンプトを1つずつ作成します。順番と件数を厳密に合わせてください。
「組み合わせ」は選ばれた材料を中心に、絵として自然で魅力的な場面を作ります。全部の材料を使う必要はなく、矛盾する場合はテーマを優先します。
「連想を広げる」は材料をきっかけに別の物・行動・状況へ飛躍してください。「自由な発想」では、groupsとrecent_ideasは重複を避けるためだけに参照してください。テーマの明示条件は守り、それ以外の場所・動作・小道具・演出は登録済みの材料や過去案の言い換えから選ばず、新しく考えてください。elementsには、その案で新しく考え、実際にプロンプトに入れた再利用可能な要素を2〜5件返してください。テーマの明示条件そのものは新しい要素として数えません。
単語を機械的に詰め込まず、一枚の絵としてまとまる意外な組み合わせにしてください。同じポーズ、背景、小道具を繰り返さず、最近の画像案とは異なる方向も探してください。
削除済みの材料・グループは使用せず、言い換えて復活させることも避けてください。採用材料は必須条件ではありません。
キャラクターのメモがある場合は外見を反映し、参照画像の服装・背景・ポーズは自動コピーしません。
各案のelementsには、その画像のプロンプトに実際に含めた具体的な要素を日本語で2〜5個挙げてください。既存にないグループも構いません。これらは人間が材料として採用するための短いラベルです。既存の材料を使った場合は、材料一覧のグループ名と値をそのまま使ってください。テーマ文全体を引用・言い換えして要素に入れないでください。場所・動作・表情・小道具など、単独で再利用できる具体的な要素を1件ずつ挙げてください。
返答は次のJSONだけ。Markdownは禁止です。
{"ideas":[{"title":"短い日本語の案名","prompt":"Complete English image prompt","elements":[{"group":"場所","value":"公園"}]}]}'''
    recent = [{'title': i['title'], 'elements': i['elements']} for b in s['batches'] for i in b['ideas']][-40:]
    base_prompt = s.get('base_prompt', '')
    if base_prompt:
        system += '\nbase_promptは発想の出発点となる完成プロンプトです。元の中心被写体や画風を手掛かりにしつつ、持ち物・動作・場所・演出を分解して別の組み合わせへ広げてください。元の全文を必須条件として扱わず、同じ場面の小変更だけに偏らないでください。削除済み材料や、今回のrecipesの方向性を優先します。'
    context = {
        'theme': '元のプロンプトを材料に多彩な画像へ発想を広げる' if base_prompt else s['theme'],
        'base_prompt': base_prompt, 'groups': s['groups'], 'removed_groups': s['removed_groups'],
        'removed_values': s['removed_values'], 'recent_ideas': recent,
        'character_notes': char.get('notes', '') if char else '',
    }

    instruction = system + (
        f'\n今回はrecipesが{amount}件あります。例は形式だけを示しています。ideasに{amount}件を入れてください。'
        '全件を返せない場合は、先頭から作成できた分だけ順番どおりに返してください。'
    )
    def validate(result):
        ideas = result.get('ideas')
        if not isinstance(ideas, list) or not 1 <= len(ideas) <= amount:
            raise ValueError('AIのプロンプト数が一致しません。')
        clean = []
        known = {i['prompt'] for b in s['batches'] for i in b['ideas']}
        for i, idea in enumerate(ideas):
            if not isinstance(idea, dict) or not isinstance(idea.get('prompt'), str) or not idea['prompt'].strip():
                raise ValueError('AIのプロンプトが空でした。')
            prompt = idea['prompt'].strip()
            if prompt in known:
                raise ValueError('AIが同じプロンプトを作成しました。')
            known.add(prompt)
            elements = idea.get('elements')
            if not isinstance(elements, list) or not elements:
                raise ValueError('AIが画像の要素を出力しませんでした。')
            clean_elements = []
            for e in elements:
                if not isinstance(e, dict) or not str(e.get('group', '')).strip() or not str(e.get('value', '')).strip():
                    raise ValueError('AIの要素の形式が不正です。')
                group, value = str(e['group']).strip()[:80], str(e['value']).strip()[:200]
                if norm(group) in {norm(x) for x in s['removed_groups']} or norm(value) in {norm(x) for x in s['removed_values']}:
                    raise ValueError('AIが削除済みの材料を使用しました。')
                clean_elements.append({'group': group, 'value': value})
            clean.append({'id': uuid.uuid4().hex[:12], 'title': str(idea.get('title') or '新しい発想')[:120],
                          'prompt': prompt, 'elements': clean_elements, 'direction': recipes[i]['direction'], 'image': None})
        return clean
    return output_retry(lambda: ai_json(bridge, instruction, {**context, 'recipes': recipes}), validate, 'ひらめき・プロンプト準備')


def latest_batch(s, batch_id):
    if not s['batches'] or s['batches'][-1]['id'] != batch_id:
        raise ValueError('最新の生成だけ再開できます。')
    return s['batches'][-1]


def prepare_prompts(s, body, bridge):
    batch = latest_batch(s, body.get('batch_id'))
    if len(batch['ideas']) < batch['target_count']:
        ideas = plan_chunk(s, batch, bridge)
        if 'commit_llm_request' in bridge:
            bridge['commit_llm_request']()
        batch['ideas'].extend(ideas)
        batch['status'] = 'pending'
        with LOCK:
            save(s)
    return {'session': s}


def next_image(s, body, bridge):
    batch = latest_batch(s, body.get('batch_id'))
    if body.get('prepared_only') is True and len(batch['ideas']) < batch['target_count']:
        raise ValueError('画像用プロンプトの準備が終わっていません。「残りを生成する」から再試行してください。')
    request_id = body.get('request_id')
    if request_id and batch.get('last_request_id') == request_id:
        return {'session': s}
    if batch['status'] == 'completed':
        return {'session': s}
    character = character_for(s, bridge)
    batch['status'] = 'pending'
    pending = next((i for i in batch['ideas'] if not i['image']), None)
    if pending is None and len(batch['ideas']) < batch['target_count']:
        ideas = plan_chunk(s, batch, bridge)
        batch['ideas'].extend(ideas)
        pending = ideas[0]
    if 'commit_llm_request' in bridge:
        bridge['commit_llm_request']()
    with LOCK:
        save(s)  # Persist the exact prompts before ComfyUI starts.
    if pending is None:
        raise ValueError('未生成の画像が見つかりません。')
    cfg = s['settings']
    info = bridge['generate_image'](pending['prompt'], cfg['width'], cfg['height'], 'new', None, None, None, 0.70, character, cfg['model'], 'inspire')
    raw = bridge['get_previous_image_bytes'](info)
    iid = uuid.uuid4().hex
    DATA.mkdir(exist_ok=True)
    (DATA / (iid + '.png')).write_bytes(raw)
    pending['image'] = {'filename': iid + '.png', 'url': '/inspire-image?id=' + iid, 'info': info}
    batch['last_request_id'] = request_id
    if sum(bool(i['image']) for i in batch['ideas']) == batch['target_count']:
        batch['status'] = 'completed'
    with LOCK:
        save(s)
    return {'session': s}


def post(path, body, bridge):
    if not WORK.acquire(blocking=False):
        raise RequestBusyError('画像生成またはAIの処理中です。完了後にお試しください。')
    try:
        if path == '/api/inspire/create':
            return create(body, bridge)
        with LOCK:
            s = load(body.get('session_id'))
        if path == '/api/inspire/expand':
            return expand(s, bridge)
        if path == '/api/inspire/prepare':
            return prepare_prompts(s, body, bridge)
        if path == '/api/inspire/next':
            return next_image(s, body, bridge)
        with LOCK:
            if path == '/api/inspire/groups':
                update_groups(s, body.get('groups'))
            elif path == '/api/inspire/start':
                count = count_value(body.get('count', s['settings']['count']))
                if s['batches'] and s['batches'][-1]['status'] == 'pending':
                    raise ValueError('残りを生成するか、中止してから次の生成を開始してください。')
                s['settings']['count'] = count
                s['batches'].append({'id': uuid.uuid4().hex[:12], 'number': len(s['batches']) + 1,
                    'target_count': count, 'status': 'pending', 'ideas': [],
                    'groups_used': json.loads(json.dumps(s['groups'], ensure_ascii=False))})
            elif path == '/api/inspire/stop':
                if s['batches'] and s['batches'][-1]['status'] != 'completed':
                    s['batches'][-1]['status'] = 'stopped'
            elif path == '/api/inspire/adopt':
                _, idea = idea_for(s, body.get('batch_id'), body.get('idea_id'))
                if not idea['image']:
                    raise ValueError('生成済みの画像から採用してください。')
                index = body.get('element_index')
                if type(index) is not int or index < 0 or index >= len(idea['elements']):
                    raise ValueError('要素が見つかりません。')
                element = idea['elements'][index]
                # Work on a copy so removals are recorded against the previous materials.
                groups = json.loads(json.dumps(s['groups'], ensure_ascii=False))
                group = adopted_element_group(groups, element)
                action = body.get('action', 'add')
                if action not in ('add', 'remove'):
                    raise ValueError('材料の操作が不正です。')
                if action == 'remove':
                    if group is not None:
                        group['values'] = [v for v in group['values'] if norm(v) != norm(element['value'])]
                else:
                    if group is None:
                        group = next((g for g in groups if norm(g['name']) == norm(element['group'])), None)
                    if group is None:
                        group = {'name': element['group'], 'values': []}
                        groups.append(group)
                    if norm(element['value']) not in {norm(v) for v in group['values']}:
                        group['values'].append(element['value'])
                update_groups(s, groups)
            elif path == '/api/inspire/copy-gallery':
                batch, idea = idea_for(s, body.get('batch_id'), body.get('idea_id'))
                if not idea['image']:
                    raise ValueError('未生成の画像はコピーできません。')
                gid = idea.get('gallery_id')
                if gid:
                    for item in bridge['load_gallery']():
                        if item.get('info', {}).get('gallery_id') == gid:
                            return {'image': item, 'already_copied': True}
                cfg = s['settings']
                metadata = {'user_instruction': s['theme'], 'final_prompt': idea['prompt'], 'mode': 'new',
                    'model': cfg['model'], 'width': cfg['width'], 'height': cfg['height'],
                    'character': cfg.get('character_name') or None, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'inspiration_session_id': s['id'], 'inspiration_batch': batch['number']}
                item = bridge['save_gallery_image'](idea['image'].get('info') or {}, metadata, image_data=image_path(idea['image']).read_bytes())
                idea['gallery_id'] = item['info']['gallery_id']
                save(s)
                return {'image': item, 'already_copied': False}
            elif path == '/api/inspire/delete':
                files = {image_path(i['image']) for b in s['batches'] for i in b['ideas'] if i['image']}
                for file in files:
                    file.unlink(missing_ok=True)
                sid_path(s['id']).unlink()
                return {'sessions': listing()}
            else:
                raise ValueError('ひらめきAPIが不正です。')
            save(s)
            return {'session': s}
    finally:
        WORK.release()


def handle_get(handler):
    parsed = urllib.parse.urlparse(handler.path)
    if parsed.path not in ('/api/inspire', '/inspire-image', '/inspiration.js', '/inspiration.css'):
        return False
    try:
        if parsed.path in ('/inspiration.js', '/inspiration.css'):
            file = Path(__file__).resolve().parent / parsed.path[1:]
            kind = 'application/javascript' if parsed.path.endswith('.js') else 'text/css'
            handler.send_bytes(200, file.read_bytes(), kind + '; charset=utf-8')
        elif parsed.path == '/inspire-image':
            iid = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
            if not re.fullmatch('[a-f0-9]{32}', iid):
                raise ValueError('画像IDが不正です。')
            handler.send_bytes(200, image_path({'filename': iid + '.png'}).read_bytes(), 'image/png')
        else:
            sid = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
            with LOCK:
                result = {'session': load(sid)} if sid else {'sessions': listing()}
            handler.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
    except Exception as e:
        handler.send_exception(e)
    return True
