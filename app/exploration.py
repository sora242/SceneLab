from storage_paths import DATA_DIR
from api_errors import RequestBusyError
from llm_output_retry import output_retry
"""Persistent, human-rated prompt exploration. Normal chat history is independent."""
import model_registry
import json
import re
import threading
import time
import uuid
import urllib.parse
from pathlib import Path

DATA = DATA_DIR / "exploration_data"
LOCK = threading.RLock()
WORK = threading.Lock()

HTML = '''<section id="exploreView" class="view"><div class="explorePage">
<div class="exploreHead"><div><div class="editTitle">探索モード</div><div class="editorHelp">同じテーマで候補を生成し、ハートの評価を次のラウンドへ反映します。</div></div><div class="exploreActions" style="margin:0"><button id="exploreNew" class="secondary">＋ 新しいテーマ</button><button id="exploreDelete" class="dangerSmall" hidden>テーマを削除</button></div></div>
<label class="exploreLabel">保存したテーマ<select id="exploreSessions" class="select"><option value="">新しいテーマ</option></select></label>
<div id="exploreSetup"><label class="exploreLabel">テーマまたは最終プロンプト<textarea id="exploreTheme" rows="4" placeholder="画像のテーマ、または元にしたい最終プロンプトを入力・貼り付け"></textarea></label>
</div><div class="exploreSettings"><label>1ラウンドの画像数<div class="stepper" style="width:170px;margin-top:6px"><button type="button" id="exploreCountMinus" aria-label="画像数を減らす">−</button><input id="exploreCount" type="number" min="1" step="1" value="4" required><button type="button" id="exploreCountPlus" aria-label="画像数を増やす">＋</button></div></label><span class="sub">1ラウンドで生成する画像数を設定します。モデル・サイズ・キャラクターは最初に選んだ設定を使います。キャラクター参照はQwen系モデル専用です。</span></div>
<div id="exploreThemeSummary"></div><div class="exploreActions"><button id="exploreStart" class="primarySmall">テーマから探索</button><button id="exploreFromPrompt" class="directSmall">最終プロンプトから探索</button><button id="exploreResetScores" class="secondary" hidden>評価を全て0に戻す</button><button id="exploreResume" class="secondary" hidden>未生成の候補を生成</button><button id="exploreStop" class="secondary" hidden>この1枚の完了後に中止</button></div>
<div id="exploreStatus" role="status" class="editorHelp"></div><div id="exploreRounds"></div>
</div></section>'''


def install_html(html):
    html = html.replace('</style>', '</style><link rel="stylesheet" href="/exploration.css">', 1)
    html = html.replace('</nav>', '<button class="tab" id="exploreTab">探索</button></nav>', 1)
    html = html.replace('</main>', HTML + '</main>', 1)
    html = html.replace('</body>', '<script src="/exploration.js"></script></body>', 1)
    return html


def session_path(sid):
    if not isinstance(sid, str) or not re.fullmatch(r'exp_[a-f0-9]{16}', sid):
        raise ValueError('テーマIDが不正です。')
    return DATA / (sid + '.json')


def load(sid):
    path = session_path(sid)
    if not path.is_file():
        raise ValueError('保存したテーマが見つかりません。')
    session = json.loads(path.read_text(encoding='utf-8'))
    for rnd in session['rounds']:
        for idea in rnd['ideas']:
            if idea.get('score') is None:
                idea['score'] = 0
    return session


def save(session):
    DATA.mkdir(exist_ok=True)
    path = session_path(session['id'])
    session['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def listing():
    items = []
    if DATA.exists():
        for path in DATA.glob('exp_*.json'):
            try:
                s = load(path.stem)
                items.append({k: s[k] for k in ('id', 'theme', 'updated_at')})
            except (ValueError, KeyError, OSError):
                continue
    return sorted(items, key=lambda x: x['updated_at'], reverse=True)


def find_idea(session, rid, iid):
    for rnd in session['rounds']:
        if rnd['id'] == rid:
            for idea in rnd['ideas']:
                if idea['id'] == iid:
                    return rnd, idea
    raise ValueError('候補が見つかりません。')


def plan(body, bridge):
    sid = body.get('session_id')
    with LOCK:
        if sid:
            s = load(sid)
            if s['rounds'] and s['rounds'][-1]['status'] not in ('completed', 'stopped'):
                raise ValueError('先に現在のラウンドの未生成候補を生成してください。')
            if not any(i['image'] for r in s['rounds'] for i in r['ideas']):
                raise ValueError('画像を1枚以上生成してから次のラウンドへ進んでください。')
        else:
            theme = str(body.get('theme', '')).strip()
            if not theme or len(theme) > 12000:
                raise ValueError('テーマを1〜12000文字で入力してください。')
            count = body.get('count', 4)
            if type(count) is not int or count < 1:
                raise ValueError('画像数は1以上の整数で指定してください。')
            model = str(body.get('model', 'qwen21_turbo'))
            model_registry.get(model)
            cid = str(body.get('character_id', ''))
            character = bridge['get_character'](cid) if cid else None
            if cid and not character:
                raise ValueError('選択したキャラクターが見つかりません。')
            s = {'id': 'exp_' + uuid.uuid4().hex[:16], 'theme': theme,
                 'base_prompt': theme if body.get('from_final_prompt') is True else '',
                 'settings': {'count': count, 'width': bridge['clamp_dimension'](body.get('width'), 1024),
                              'height': bridge['clamp_dimension'](body.get('height'), 1024),
                              'model': model, 'character_id': cid,
                              'character_name': character['name'] if character else ''}, 'rounds': []}
        # Freeze the feedback used for this request, even if ratings are edited later.
        feedback = [{'round': r['number'], 'prompt': i['prompt'], 'score': i['score'], 'comment': i['comment']}
                    for r in s['rounds'] for i in r['ideas'] if i['image']]
        settings = dict(s['settings'])
        count = body.get('count', settings['count'])
        if type(count) is not int or count < 1:
            raise ValueError('画像数は1以上の整数で指定してください。')
        settings['count'] = count
    character = bridge['get_character'](settings['character_id']) if settings['character_id'] else None
    if settings['character_id'] and not character:
        raise ValueError('このテーマで使用するキャラクターが削除されています。')
    system = '''あなたは画像生成の候補プロンプトを設計するアシスタントです。
固定のテーマを守りつつ、構図・照明・ポーズ・背景などに意味のある違いのある英語プロンプトを指定数作成してください。
人間の評価はハート0〜4です。0は最低評価、4は最高評価です。評価されていない画像も0として扱います。
過去のプロンプトと評価・コメントを参考に、高評価の要素を発展させ、低評価の要素は避けてください。
低評価の理由は断定せず、コメントの明示的な好みを優先してください。一部の候補には新しい方向性も残してください。
ユーザーのテーマと人数・服装等の明示的な条件が最優先です。各プロンプトは単独で画像モデルに渡せる完成形にし、過去の画像を参照する省略表現を使わないでください。
画像は見ることができません。画像を見たかのような主張はしないでください。
キャラクター指定がある場合は外見メモを反映し、参照画像の背景やポーズを自動でコピーしないでください。
返答は次のJSONだけにしてください。Markdownは禁止です。
{"ideas":[{"title":"短い日本語の案名","prompt":"Complete English image prompt"}]}'''
    base_prompt = s.get('base_prompt', '')
    if base_prompt:
        system += '\nbase_promptは既存画像の生成に使われた完成プロンプトです。新しいテーマとして大きく解釈し直さず、このプロンプトを土台に派生案を作ってください。主題・人数・外見・画風・主要な雰囲気は引き継ぎ、構図・動作・照明など一部の要素を変え、各案で変化の方向を分けてください。過去の評価・コメントで変更を求められた部分はそちらを優先します。完成プロンプトは毎回単独で使える形にしてください。'
    context = {'theme': s['theme'], 'base_prompt': base_prompt, 'count': settings['count'], 'past_results': feedback,
               'character': {'name': character['name'], 'notes': character.get('notes', '')} if character else None}
    def request():
        return bridge['llm_json']([
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)},
    ], temperature=0.7)
    def validate(result):
        ideas = result.get('ideas')
        if not isinstance(ideas, list) or len(ideas) != settings['count']:
            raise ValueError('AIの候補数が指定数と一致しません。')
        clean = []
        for i, idea in enumerate(ideas):
            if not isinstance(idea, dict) or not isinstance(idea.get('prompt'), str) or not idea['prompt'].strip():
                raise ValueError('AIの候補に空のプロンプトがありました。')
            clean.append({'id': uuid.uuid4().hex[:12], 'title': str(idea.get('title') or f'候補{i+1}')[:120],
                          'prompt': idea['prompt'].strip(), 'score': 0, 'comment': '', 'image': None})
        if len({i['prompt'] for i in clean}) != len(clean):
            raise ValueError('AIが同じプロンプトを重複作成しました。')
        return clean
    clean = output_retry(request, validate, '探索・プロンプト準備')
    with LOCK:
        if sid:
            s = load(sid)
        if 'commit_llm_request' in bridge:
            bridge['commit_llm_request']()
        s['settings']['count'] = settings['count']
        s['rounds'].append({'id': uuid.uuid4().hex[:12], 'number': len(s['rounds']) + 1,
                            'status': 'pending', 'ideas': clean, 'feedback_used': feedback})
        save(s)
    return {'session': s}


def generate(body, bridge):
    with LOCK:
        s = load(body.get('session_id'))
        rnd, idea = find_idea(s, body.get('round_id'), body.get('idea_id'))
        if idea['image']:
            return {'session': s}  # A retry never generates a completed candidate twice.
        if rnd['status'] == 'completed' or rnd['id'] != s['rounds'][-1]['id']:
            raise ValueError('このラウンドは終了しています。')
        if rnd['status'] == 'stopped':
            rnd['status'] = 'pending'
            save(s)
        cfg = s['settings']
    character = bridge['get_character'](cfg['character_id']) if cfg['character_id'] else None
    if cfg['character_id'] and not character:
        raise ValueError('キャラクターが見つかりません。')
    info = bridge['generate_image'](idea['prompt'], cfg['width'], cfg['height'], 'new', None, None, None, 0.70, character, cfg['model'], 'explore')
    image_id = uuid.uuid4().hex
    DATA.mkdir(exist_ok=True)
    # Keep an independent copy: gallery cleanup must not erase rated exploration results.
    image_file = DATA / (image_id + '.png')
    image_file.write_bytes(bridge['get_previous_image_bytes'](info))
    image = {'url': '/explore-image?id=' + image_id, 'filename': image_file.name, 'info': info}
    with LOCK:
        s = load(body.get('session_id'))
        rnd, idea = find_idea(s, body.get('round_id'), body.get('idea_id'))
        idea['image'] = image
        if all(i['image'] for i in rnd['ideas']):
            rnd['status'] = 'completed'
        save(s)
    return {'session': s}


def delete_session(sid):
    with LOCK:
        session = load(sid)
        files = set()
        for rnd in session['rounds']:
            for idea in rnd['ideas']:
                image = idea.get('image')
                if not image:
                    continue
                name = image.get('filename', '')
                if not isinstance(name, str) or not re.fullmatch(r'[a-f0-9]{32}\.png', name):
                    raise ValueError('保存画像のファイル名が不正なため削除を中止しました。')
                target = DATA / name
                if target.resolve().parent != DATA.resolve():
                    raise ValueError('保存先の範囲外にある画像は削除できません。')
                files.add(target)
        for target in files:
            target.unlink(missing_ok=True)
        session_path(sid).unlink()
        return {'sessions': listing()}


def post(path, body, bridge):
    if path in ('/api/explore/plan', '/api/explore/generate', '/api/explore/delete'):
        if not WORK.acquire(blocking=False):
            raise RequestBusyError('探索モードで処理中です。完了後にもう一度お試しください。')
        try:
            if path == '/api/explore/delete':
                return delete_session(body.get('session_id'))
            return plan(body, bridge) if path.endswith('/plan') else generate(body, bridge)
        finally:
            WORK.release()
    with LOCK:
        s = load(body.get('session_id'))
        if path == '/api/explore/reset-scores':
            if WORK.locked():
                raise ValueError('現在の処理が完了してから評価をリセットしてください。')
            for rnd in s['rounds']:
                for idea in rnd['ideas']:
                    idea['score'] = 0
            save(s)
            return {'session': s}
        if path == '/api/explore/copy-gallery':
            rnd, idea = find_idea(s, body.get('round_id'), body.get('idea_id'))
            image = idea.get('image')
            if not image:
                raise ValueError('未生成の画像はコピーできません。')
            previous_id = idea.get('gallery_id')
            if previous_id:
                for item in bridge['load_gallery']():
                    if item.get('info', {}).get('gallery_id') == previous_id:
                        return {'image': item, 'already_copied': True}
            name = image.get('filename', '')
            if not isinstance(name, str) or not re.fullmatch(r'[a-f0-9]{32}\.png', name):
                raise ValueError('保存画像のファイル名が不正です。')
            file = DATA / name
            if file.resolve().parent != DATA.resolve():
                raise ValueError('画像の保存先が不正です。')
            cfg = s['settings']
            metadata = {
                'user_instruction': s['theme'], 'final_prompt': idea['prompt'],
                'model': cfg['model'], 'mode': 'new', 'width': cfg['width'], 'height': cfg['height'],
                'character': cfg.get('character_name') or None,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'exploration_session_id': s['id'], 'exploration_round': rnd['number'],
                'exploration_title': idea['title']
            }
            copied = bridge['save_gallery_image'](image.get('info') or {}, metadata, image_data=file.read_bytes())
            idea['gallery_id'] = copied['info']['gallery_id']
            save(s)
            return {'image': copied, 'already_copied': False}
        if path == '/api/explore/rate':
            _, idea = find_idea(s, body.get('round_id'), body.get('idea_id'))
            if not idea['image']:
                raise ValueError('未生成の画像は評価できません。')
            score = body.get('score')
            if score is None:
                score = 0
            if type(score) is not int or score < 0 or score > 4:
                raise ValueError('評価はハート0〜4で指定してください。')
            idea['score'] = score
            idea['comment'] = str(body.get('comment', ''))[:2000]
        elif path == '/api/explore/stop':
            if WORK.locked():
                raise ValueError('現在の処理が完了してから中止してください。')
            if s['rounds'] and s['rounds'][-1]['status'] != 'completed':
                s['rounds'][-1]['status'] = 'stopped'
        else:
            raise ValueError('探索APIが不正です。')
        save(s)
        return {'session': s}


def handle_get(handler):
    parsed = urllib.parse.urlparse(handler.path)
    path = parsed.path
    if path not in ('/api/explore', '/explore-image', '/exploration.css', '/exploration.js'):
        return False
    try:
        if path in ('/exploration.css', '/exploration.js'):
            file = Path(__file__).resolve().parent / path[1:]
            handler.send_bytes(200, file.read_bytes(), 'text/css; charset=utf-8' if path.endswith('.css') else 'application/javascript; charset=utf-8')
        elif path == '/explore-image':
            iid = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
            if not re.fullmatch('[a-f0-9]{32}', iid):
                raise ValueError('画像IDが不正です。')
            handler.send_bytes(200, (DATA / (iid + '.png')).read_bytes(), 'image/png')
        else:
            sid = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
            with LOCK:
                data = {'session': load(sid)} if sid else {'sessions': listing()}
            handler.send_bytes(200, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
    except Exception as e:
        handler.send_exception(e)
    return True
