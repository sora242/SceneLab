from storage_paths import DATA_DIR
"""Model profiles, local overrides and ComfyUI capability checks (stdlib only)."""
import user_workflows
import copy
import json
import math
import uuid
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = DATA_DIR / 'model_settings.local.json'
CUSTOM_MODELS = DATA_DIR / 'custom_models.local.json'
LOCK = threading.RLock()
FILE_INPUTS = {'diffusion': ('UNETLoader', 'unet_name'), 'text_encoder': ('CLIPLoader', 'clip_name'), 'vae': ('VAELoader', 'vae_name')}

def definitions():
    result = {}
    for path in sorted((ROOT / 'models').glob('*.json')):
        model = json.loads(path.read_text(encoding='utf-8'))
        if model['id'] in result or model['backend'] not in ('qwen21', 'z_image', 'anima'):
            raise ValueError('モデル定義が不正です: ' + path.name)
        result[model['id']] = model
    if CUSTOM_MODELS.exists():
        for model in json.loads(CUSTOM_MODELS.read_text(encoding='utf-8')):
            if model['id'] in result or not model['id'].startswith('custom_') or model['backend'] not in ('qwen21', 'z_image', 'anima', 'user_workflow'):
                raise ValueError('追加モデル定義が不正です。')
            result[model['id']] = model
    return result

def manage_custom(body, url):
    with LOCK:
        models = definitions()
        custom = [m for m in models.values() if m.get('custom') is True]
        if body['action'] == 'remove_custom':
            model_id = body.get('id')
            if model_id not in models or not models[model_id].get('custom'):
                raise ValueError('追加したモデルだけ削除できます。')
            custom = [m for m in custom if m['id'] != model_id]
        elif body['action'] == 'add_workflow':
            label = body.get('label')
            if not isinstance(label,str) or not 1 <= len(label.strip()) <= 80:
                raise ValueError('表示名を1〜80文字で指定してください。')
            graph, bindings, output = body.get('graph'), body.get('bindings'), body.get('output_node')
            user_workflows.validate(graph, bindings, output)
            model = dict(id='custom_'+uuid.uuid4().hex, label=label.strip(), custom=True, backend='user_workflow',
                         files={}, sampling={}, capabilities=dict(generate=True,edit=False,edit_strength=False,character_reference=False),
                         graph=graph, bindings=bindings, output_node=output, required_nodes=[])
            report = user_workflows.check(model, inventory(url))
            if not report['ready']:
                raise ValueError('\n'.join(report['errors']))
            custom.append(model)
        elif body['action'] == 'add_custom':
            base = models.get(body.get('base_id'))
            label = body.get('label')
            if not base or base.get('custom') or not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                raise ValueError('元にする対応モデルと1〜80文字の表示名を指定してください。')
            model = copy.deepcopy(base)
            model.update(id='custom_' + uuid.uuid4().hex, label=label.strip(), custom=True, base_id=base['id'])
            model['files'] = body.get('files')
            if not isinstance(model['files'], dict):
                raise ValueError('モデルファイルを指定してください。')
            validate_values(model)
            report = check(model, inventory(url))
            if not report['ready']:
                raise ValueError('\n'.join(report['errors']))
            custom.append(model)
        else:
            raise ValueError('モデルの操作が不正です。')
        temporary = CUSTOM_MODELS.with_suffix('.tmp')
        temporary.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(CUSTOM_MODELS)
    return {'saved': True}

def overrides():
    with LOCK:
        return json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}

def get(model_id, parameter_mode=None):
    models = definitions()
    if model_id not in models:
        raise ValueError('未対応の画像モデルです: ' + str(model_id))
    model = copy.deepcopy(models[model_id])
    override = overrides().get(model_id, {})
    for key in ('files', 'sampling', 'edit_sampling'):
        if key in model:
            model[key].update(override.get(key, {}))
    model['mode_parameters'] = copy.deepcopy(override.get('mode_parameters', {}))
    if parameter_mode is not None:
        if parameter_mode not in ('chat', 'edit', 'explore', 'inspire'):
            raise ValueError('パラメータのモードが不正です。')
        group = 'edit_sampling' if parameter_mode == 'edit' and 'edit_sampling' in model else 'sampling'
        values = model['mode_parameters'].get(parameter_mode, {})
        model['seed'] = values.get('seed')
        model[group].update({key: value for key, value in values.items() if key != 'seed'})
    return model

def listing():
    return [get(key) for key in definitions()]

def inventory(url):
    with urllib.request.urlopen(url + '/object_info', timeout=10) as response:
        return json.load(response)

def choices(info, node, field):
    spec = info.get(node, {}).get('input', {}).get('required', {}).get(field)
    if spec is None:
        spec = info.get(node, {}).get('input', {}).get('optional', {}).get(field)
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    if isinstance(spec[0], (list, tuple)):
        return list(spec[0])
    if spec[0] == 'COMBO' and len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get('options', [])
        return list(options) if isinstance(options, (list, tuple)) else []
    return []

def check(model, info):
    if model['backend'] == 'user_workflow':
        return user_workflows.check(model, info)
    errors = []
    for node in model['required_nodes']:
        if node not in info:
            errors.append('必要なノードがありません: ' + node)
    for key, (node, field) in FILE_INPUTS.items():
        if model['files'][key] not in choices(info, node, field):
            errors.append(key + ' のファイルが見つかりません: ' + model['files'][key])
    for key in ('sampler_name', 'scheduler'):
        if model['sampling'][key] not in choices(info, 'KSampler', key):
            errors.append('未対応の生成設定: ' + key + '=' + model['sampling'][key])
    if 'edit_sampling' in model and model['edit_sampling']['sampler_name'] not in choices(info, 'KSamplerSelect', 'sampler_name'):
        errors.append('編集用Samplerが未対応です: ' + model['edit_sampling']['sampler_name'])
    return {'ready': not errors, 'errors': errors}

def status(url):
    models = listing()
    try:
        info = inventory(url)
        return {'connected': True, 'url': url, 'files': {key: choices(info, *spec) for key, spec in FILE_INPUTS.items()},
                'edit_samplers': choices(info, 'KSamplerSelect', 'sampler_name'),
                'samplers': choices(info, 'KSampler', 'sampler_name'), 'schedulers': choices(info, 'KSampler', 'scheduler'),
                'models': [{**m, 'check': check(m, info)} for m in models]}
    except (OSError, ValueError) as exc:
        return {'connected': False, 'url': url, 'error': str(exc), 'models': [{**m, 'check': None} for m in models]}

def parse_edit_sigmas(text):
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError('編集用Sigmasはカンマ区切りの数値で指定してください。')
    try:
        values = [float(v.strip()) for v in text.split(',')]
    except ValueError:
        raise ValueError('編集用Sigmasはカンマ区切りの数値で指定してください。')
    if not 2 <= len(values) <= 201 or any(not math.isfinite(v) or v < 0 for v in values) or values[-1] != 0 or values[0] <= 0 or any(a < b for a,b in zip(values,values[1:])):
        raise ValueError('Sigmasは非負・降順、末尾0の2〜201個の数値にしてください。ステップ数は数値の個数−1です。')
    return values


def validate_values(model):
    if model['backend'] == 'user_workflow':
        user_workflows.validate(model['graph'],model['bindings'],model['output_node'])
        if model['sampling'] or model['files']:
            raise ValueError('このワークフローのパラメータはJSON内で変更してください。')
        return
    if set(model['files']) != set(FILE_INPUTS) or set(model['sampling']) != {'steps', 'cfg', 'sampler_name', 'scheduler'}:
        raise ValueError('設定項目が不正です。')
    if 'edit_sampling' in model:
        edit = model['edit_sampling']
        if set(edit) != {'cfg', 'sampler_name', 'sigmas'}:
            raise ValueError('編集用の設定項目が不正です。')
        if type(edit['cfg']) not in (int,float) or not math.isfinite(edit['cfg']) or not 0 <= edit['cfg'] <= 30:
            raise ValueError('編集用CFGは0〜30にしてください。')
        if not isinstance(edit['sampler_name'],str) or not edit['sampler_name']:
            raise ValueError('編集用Samplerが不正です。')
        parse_edit_sigmas(edit['sigmas'])
    for value in model['files'].values():
        if not isinstance(value, str) or not value or len(value) > 500 or value.lower().endswith('.gguf'):
            raise ValueError('標準ローダーで使えるモデルファイルを選択してください。GGUFは未対応です。')
    sampling = model['sampling']
    if type(sampling['steps']) is not int or not 1 <= sampling['steps'] <= 200:
        raise ValueError('stepsは1〜200の整数にしてください。')
    if type(sampling['cfg']) not in (int, float) or not math.isfinite(sampling['cfg']) or not 0 <= sampling['cfg'] <= 30:
        raise ValueError('CFGは0〜30にしてください。')
    for key in ('sampler_name', 'scheduler'):
        if not isinstance(sampling[key], str) or not sampling[key] or len(sampling[key]) > 100:
            raise ValueError('生成設定が不正です。')

def save(body, url):
    if 'action' in body:
        return manage_custom(body, url)
    if 'parameter_mode' in body:
        return save_mode(body)
    model_id = body.get('id')
    model = get(model_id)
    reset_group = body.get('reset_group')
    if reset_group is not None:
        if reset_group not in ('files', 'sampling', 'edit_sampling') or reset_group not in model:
            raise ValueError('初期化するパラメータが不正です。')
        model[reset_group] = copy.deepcopy(definitions()[model_id][reset_group])
        validate_values(model)
    elif body.get('reset') is not True:
        for key in ('files', 'sampling'):
            if not isinstance(body.get(key), dict):
                raise ValueError('設定の形式が不正です。')
            model[key] = body[key]
        if 'edit_sampling' in body:
            if 'edit_sampling' not in model or not isinstance(body['edit_sampling'],dict):
                raise ValueError('編集用の設定形式が不正です。')
            model['edit_sampling'] = body['edit_sampling']
        validate_values(model)
        report = check(model, inventory(url))
        if not report['ready']:
            raise ValueError('\n'.join(report['errors']))
    with LOCK:
        settings = overrides()
        if body.get('reset') is True:
            settings.pop(model_id, None)
        else:
            settings.setdefault(model_id, {}).update({key: model[key] for key in ('files', 'sampling', 'edit_sampling') if key in model})
        temporary = SETTINGS.with_suffix('.tmp')
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(SETTINGS)
    return {'saved': True, 'model': get(model_id)}

def ensure_ready(model, url):
    validate_values(model)
    report = check(model, inventory(url))
    if not report['ready']:
        raise ValueError('画像モデルの設定を確認してください。\n' + '\n'.join(report['errors']))


def save_mode(body):
    model_id, mode = body.get('id'), body.get('parameter_mode')
    if mode not in ('chat','edit','explore','inspire'):
        raise ValueError('パラメータのモードが不正です。')
    with LOCK:
        model = get(model_id)
        group = 'edit_sampling' if mode == 'edit' and 'edit_sampling' in model else 'sampling'
        values = copy.deepcopy(definitions()[model_id][group]) if body.get('reset') is True else body.get('parameters')
        if not isinstance(values, dict):
            raise ValueError('パラメータの形式が不正です。')
        if 'seed' in values:
            seed = values['seed']
            if model['backend'] == 'user_workflow' and 'seed' not in model['bindings']:
                raise ValueError('このワークフローにはseedの入力先がありません。')
            if seed is not None and (type(seed) is not int or not 0 <= seed <= 2 ** 53 - 1):
                raise ValueError('Seedは0〜9007199254740991の整数にしてください。')
        model[group] = {key: value for key, value in values.items() if key != 'seed'}
        validate_values(model)
        settings = overrides()
        settings.setdefault(model_id, {}).setdefault('mode_parameters', {})[mode] = values
        temporary = SETTINGS.with_suffix('.tmp')
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(SETTINGS)
    return {'saved':True, 'model':get(model_id)}
