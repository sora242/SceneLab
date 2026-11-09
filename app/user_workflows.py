"""User API graphs: explicit scalar input bindings, no implicit rewriting."""
import copy
import math

FIELDS = {'prompt': str, 'seed': int, 'width': int, 'height': int}

def validate(graph, bindings, output):
    if not isinstance(graph, dict) or not graph or len(graph) > 2000:
        raise ValueError('ComfyUIのAPI形式JSONを指定してください（通常の画面保存JSONは使えません）。')
    for key, node in graph.items():
        if not isinstance(key, str) or not isinstance(node, dict) or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
            raise ValueError('ComfyUIのAPI形式JSONが不正です。')
    if not isinstance(bindings, dict) or not bindings.get('prompt') or set(bindings) - set(FIELDS):
        raise ValueError('プロンプトの入力先を指定してください。')
    used = set()
    for name, target in bindings.items():
        if not isinstance(target, list) or len(target) != 2 or not all(isinstance(v, str) for v in target):
            raise ValueError('入力先の形式が不正です。')
        node, field = target
        value = graph.get(node, {}).get('inputs', {}).get(field)
        if type(value) is not FIELDS[name] or (type(value) is float and not math.isfinite(value)):
            raise ValueError('入力先は文字列または整数の直接入力項目を選んでください。配線された項目は選べません。')
        if tuple(target) in used:
            raise ValueError('同じ入力先を重複指定できません。')
        used.add(tuple(target))
    if graph.get(output, {}).get('class_type') not in ('SaveImage', 'PreviewImage'):
        raise ValueError('結果のSaveImageまたはPreviewImageを指定してください。')
    active = set()
    visiting = set()
    def visit(key):
        if key in visiting:
            raise ValueError('ワークフローに循環した配線があります。')
        if key in active:
            return
        visiting.add(key)
        for value in graph[key]['inputs'].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0],str) and type(value[1]) is int:
                if value[0] not in graph:
                    raise ValueError('配線先のノードがありません。')
                visit(value[0])
        visiting.remove(key)
        active.add(key)
    visit(output)
    if any(target[0] not in active for target in bindings.values()):
        raise ValueError('入力先は選択した出力につながるノードを選んでください。')
    return active

def check(profile, info):
    active = validate(profile['graph'], profile['bindings'], profile['output_node'])
    errors = []
    for key in active:
        node = profile['graph'][key]
        spec = info.get(node['class_type'])
        if spec is None:
            errors.append('必要なノードがありません: '+node['class_type'])
            continue
        for group in ('required', 'optional'):
            for field, field_spec in spec.get('input', {}).get(group, {}).items():
                value = node['inputs'].get(field)
                if group == 'required' and field not in node['inputs']:
                    errors.append(node['class_type']+' の入力がありません: '+field)
                if isinstance(value,list):
                    continue
                options = field_spec[0] if isinstance(field_spec,(list,tuple)) and field_spec else None
                if options == 'COMBO' and len(field_spec)>1:
                    options = field_spec[1].get('options')
                if isinstance(options,(list,tuple)) and value is not None and value not in options:
                    errors.append(node['class_type']+' / '+field+' が見つかりません: '+str(value))
    return {'ready':not errors, 'errors':errors}

def build(profile, prompt, width, height, seed):
    active = validate(profile['graph'], profile['bindings'], profile['output_node'])
    graph = {k:copy.deepcopy(v) for k,v in profile['graph'].items() if k in active}
    values = dict(prompt=prompt,width=width,height=height,seed=seed)
    for field, (node, name) in profile['bindings'].items():
        graph[node]['inputs'][name] = values[field]
    return graph
