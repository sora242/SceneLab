"""Backend-specific workflow construction; independent of HTTP/UI."""
import model_registry

def configure_z(workflow, profile):
    for key, node, field in [('diffusion','57:28','unet_name'),('text_encoder','57:30','clip_name'),('vae','57:29','vae_name')]:
        workflow[node]['inputs'][field] = profile['files'][key]
    workflow['57:3']['inputs'].update(profile['sampling'])

def _qwen_base_nodes(prompt, width, height, seed, ref_names=None, source_name=None, profile=None):
    profile = profile or model_registry.get("qwen21_turbo")
    ref_names = list(ref_names or [])
    nodes = {
        "1": {"inputs": {"unet_name": profile["files"]["diffusion"], "weight_dtype": "default"}, "class_type": "UNETLoader", "_meta": {"title": profile["label"]}},
        "2": {"inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}, "class_type": "QwenImage21Cache", "_meta": {"title": "Qwen 2.1 Cache"}},
        "3": {"inputs": {"clip_name": profile["files"]["text_encoder"], "type": "qwen_image", "device": "default"}, "class_type": "CLIPLoader", "_meta": {"title": "Qwen3-VL"}},
        "4": {"inputs": {"vae_name": profile["files"]["vae"]}, "class_type": "VAELoader", "_meta": {"title": "Qwen 2.1 VAE"}},
    }
    encode_inputs = {"clip": ["3", 0], "prompt": prompt, "negative_prompt": "", "resolution": 0 if source_name else 1024}
    next_id = 20
    image_index = 1
    if source_name:
        nodes[str(next_id)] = {"inputs": {"image": source_name, "upload": "image"}, "class_type": "LoadImage", "_meta": {"title": "Edit target"}}
        encode_inputs["images.image_1"] = [str(next_id), 0]
        next_id += 1
        image_index = 2
        encode_inputs["vae"] = ["4", 0]
    elif ref_names:
        encode_inputs["vae"] = ["4", 0]
    for name in ref_names:
        nodes[str(next_id)] = {"inputs": {"image": name, "upload": "image"}, "class_type": "LoadImage", "_meta": {"title": f"Character reference {image_index}"}}
        encode_inputs[f"images.image_{image_index}"] = [str(next_id), 0]
        next_id += 1
        image_index += 1
    nodes["5"] = {"inputs": encode_inputs, "class_type": "TextEncodeQwenImage21", "_meta": {"title": "Qwen Image 2.1 Conditioning"}}
    if profile.get('upsamplers', {}).get('edit' if source_name else 'generate'):
        nodes['5']['inputs']['prompt'] = add_qwen_upsampler(nodes, prompt, profile, 'edit' if source_name else 'generate',
            [str(i) for i in range(20, next_id)])
    return nodes, next_id




def build_turbo_edit(prompt, seed, source_name, profile):
    """Adapt the downloaded official API graph; preserve its sampler/sigmas."""
    import json
    from pathlib import Path
    relative = profile['workflows']['edit']
    workflow_root = (Path(__file__).resolve().parent / 'workflows').resolve()
    path = (workflow_root / relative).resolve()
    if path.parent != workflow_root:
        raise ValueError('ワークフローの保存先が不正です。')
    graph = json.loads(path.read_text(encoding='utf-8'))
    expected = {'470':'LoadImage', '461':'SaveImageAdvanced', '459:451':'UNETLoader',
                '459:453':'CLIPLoader', '459:454':'VAELoader', '459:474':'TextEncodeQwenImage21',
                '459:502':'ManualSigmas', '459:503':'KSamplerSelect', '459:504':'RandomNoise',
                '459:505':'SamplerCustomAdvanced', '459:507':'CFGGuider'}
    for node, kind in expected.items():
        if graph.get(node, {}).get('class_type') != kind:
            raise ValueError('公式Turbo編集フローの構成が変わっています: ' + node)
    graph['470']['inputs']['image'] = source_name
    graph['459:451']['inputs']['unet_name'] = profile['files']['diffusion']
    graph['459:453']['inputs']['clip_name'] = profile['files']['text_encoder']
    graph['459:454']['inputs']['vae_name'] = profile['files']['vae']
    graph['459:474']['inputs']['prompt'] = (add_qwen_upsampler(graph, prompt, profile, 'edit', ['470'])
        if profile.get('upsamplers', {}).get('edit') else prompt)
    # Official default: source-sized latent from the image encoder, no custom canvas.
    graph['459:505']['inputs']['latent_image'] = ['459:474', 2]
    graph['459:504']['inputs']['noise_seed'] = seed
    if 'edit_sampling' in profile:
        edit = profile['edit_sampling']
        model_registry.parse_edit_sigmas(edit['sigmas'])
        graph['459:502']['inputs']['sigmas'] = edit['sigmas']
        graph['459:503']['inputs']['sampler_name'] = edit['sampler_name']
        graph['459:507']['inputs']['cfg'] = edit['cfg']

    graph['461']['inputs']['filename_prefix'] = 'FlashNext_Qwen21Turbo_Edit'
    # Follow actual output dependencies. This omits unused prompt enhancement,
    # comparison previews and custom-size branches without requiring their models.
    used = set()
    def visit(node_id):
        if node_id in used:
            return
        used.add(node_id)
        for value in graph[node_id]['inputs'].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in graph and type(value[1]) is int:
                visit(value[0])
    visit('461')
    return {node: graph[node] for node in graph if node in used}


def build_anima(prompt, width, height, seed, profile, source_name=None, denoise=0.7, mask_name=None):
    """Anima base workflow; add img2img/inpaint nodes only when requested."""
    import json
    from pathlib import Path
    root = (Path(__file__).resolve().parent / 'workflows').resolve()
    path = (root / profile['workflows']['generate']).resolve()
    if path.parent != root:
        raise ValueError('ワークフローの保存先が不正です。')
    graph = json.loads(path.read_text(encoding='utf-8'))
    graph['78']['inputs']['unet_name'] = profile['files']['diffusion']
    graph['71']['inputs']['clip_name'] = profile['files']['text_encoder']
    graph['72']['inputs']['vae_name'] = profile['files']['vae']
    graph['77']['inputs']['text'] = prompt
    if source_name:
        graph.pop('74', None)
        graph['100'] = {'class_type': 'LoadImage', 'inputs': {'image': source_name}}
        if mask_name:
            graph['102'] = {'class_type': 'LoadImage', 'inputs': {'image': mask_name}}
            graph['103'] = {'class_type': 'ImageToMask', 'inputs': {'image': ['102', 0], 'channel': 'red'}}
            graph['104'] = {'class_type': 'VAEEncodeForInpaint', 'inputs': {
                'pixels': ['100', 0], 'vae': ['72', 0], 'mask': ['103', 0], 'grow_mask_by': 8}}
            graph['76']['inputs']['latent_image'] = ['104', 0]
        else:
            graph['101'] = {'class_type': 'VAEEncode', 'inputs': {'pixels': ['100', 0], 'vae': ['72', 0]}}
            graph['76']['inputs']['latent_image'] = ['101', 0]
        graph['46']['inputs']['filename_prefix'] += '_Inpaint' if mask_name else '_Img2Img'
        graph['76']['inputs']['denoise'] = 1.0 if mask_name else max(0.1, min(1.0, float(denoise)))
        if mask_name:
            graph['102']['inputs']['image'] = mask_name
    else:
        graph['74']['inputs'].update(width=width, height=height)
    graph['76']['inputs'].update(profile['sampling'])
    graph['76']['inputs']['seed'] = seed
    if profile.get('filename_prefix'):
        graph['46']['inputs']['filename_prefix'] = profile['filename_prefix'] + ('_Inpaint' if mask_name else '_Img2Img' if source_name else '')
    return graph


def add_qwen_upsampler(graph, prompt, profile, mode, image_nodes=()):
    """Use official Qwen PE settings, selecting only the requested model."""
    import copy
    import json
    from pathlib import Path
    filename = profile['upsamplers'][mode]
    edit = mode == 'edit'
    template = 'image_qwen_image_2_1_turbo_' + ('image_edit' if edit else 't2i') + '.json'
    source = json.loads((Path(__file__).resolve().parent / 'workflows' / template).read_text(encoding='utf-8'))
    generate_id, system_id, loader_id = ('459:500', '459:479', '459:477') if edit else ('459:471', '459:475', '459:473')
    prefix = 'qwen_pe'
    while any(key.startswith(prefix) for key in graph):
        prefix += '_'
    loader, system, generate = [prefix + '_' + name for name in ('loader', 'system', 'generate')]
    graph[loader] = copy.deepcopy(source[loader_id])
    graph[loader]['inputs']['clip_name'] = filename
    graph[system] = copy.deepcopy(source[system_id])
    graph[generate] = copy.deepcopy(source[generate_id])
    inputs = graph[generate]['inputs']
    inputs.update(prompt=prompt, clip=[loader, 0], system_prompt=[system, 0])
    inputs.pop('image', None)
    if image_nodes:
        batch = prefix + '_images'
        graph[batch] = {'class_type': 'BatchImagesNode', 'inputs': {
            'images.image' + str(i): [key, 0] for i, key in enumerate(image_nodes)}}
        inputs['image'] = [batch, 0]
    return [generate, 0]


def validate_edit_region(region):
    """A single rectangle in source-image fractions, independent of display size."""
    import math
    if region is None:
        return None
    if not isinstance(region, dict) or set(region) != {'x', 'y', 'width', 'height'}:
        raise ValueError('矩形の指定が不正です。範囲を描き直してください。')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in region.values()):
        raise ValueError('矩形の座標が不正です。')
    x, y, w, h = (region[k] for k in ('x', 'y', 'width', 'height'))
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise ValueError('矩形は画像の内側に指定してください。')
    # Flux layout coordinates use a 0–1000 grid. Reject collapsed selections.
    if round(x * 1000) >= round(min(1, x+w) * 1000) or round(y * 1000) >= round(min(1, y+h) * 1000):
        raise ValueError('矩形が小さすぎます。範囲を広げてください。')
    return dict(x=float(x), y=float(y), width=min(w, 1-x), height=min(h, 1-y))


def build_flux3(prompt, width, height, seed, profile, source_name=None, edit_region=None):
    """Adapt official distilled graphs using only selected PE dependencies."""
    import json
    from pathlib import Path
    edit = source_name is not None
    edit_region = validate_edit_region(edit_region)
    if edit_region and not edit:
        raise ValueError('矩形指定には編集元画像が必要です。')
    mode = 'edit' if edit else 'generate'
    root = (Path(__file__).resolve().parent / 'workflows').resolve()
    path = (root / profile['workflows'][mode]).resolve()
    if path.parent != root:
        raise ValueError('ワークフローの保存先が不正です。')
    graph = json.loads(path.read_text(encoding='utf-8'))
    unet, clip, vae, encode, sampler = ('1','2','3','4','7') if edit else ('18:1','18:2','18:3','18:4','18:7')
    for key, kind in [(unet,'UNETLoader'),(clip,'CLIPLoader'),(vae,'VAELoader'),(encode,'CLIPTextEncode'),(sampler,'KSampler'),('9','SaveImage')]:
        if graph.get(key, {}).get('class_type') != kind:
            raise ValueError('公式FLUX 3フローの構成が変わっています: ' + key)
    graph[unet]['inputs']['unet_name'] = profile['files']['diffusion']
    graph[clip]['inputs']['clip_name'] = profile['files']['text_encoder']
    graph[vae]['inputs']['vae_name'] = profile['files']['vae']
    graph[sampler]['inputs'].update(profile['sampling'], seed=seed)
    if edit:
        graph['11']['inputs']['image'] = source_name
    else:
        graph['18:6']['inputs'].update(width=width, height=height)
    filename = profile.get('upsamplers', {}).get(mode, '')
    if filename:
        loader, generate, system = ('46','47','48') if edit else ('18:12','18:11','18:14')
        graph[loader]['inputs']['clip_name'] = filename
        graph[generate]['inputs'].update(prompt=prompt, clip=[loader,0], system_prompt=[system,0])
        graph[encode]['inputs']['text'] = [generate, 0]
    else:
        graph[encode]['inputs']['text'] = prompt
    if edit_region:
        # Same CreateBoundingBoxes -> Flux3LayoutPrompt path as the official template.
        # A normalized 1000px canvas avoids dependence on preview/source dimensions.
        graph['33'] = {'class_type': 'CreateBoundingBoxes', 'inputs': {
            'width': 1000, 'height': 1000,
            'editor_state': {'__value__': [dict(x=edit_region['x']*1000, y=edit_region['y']*1000,
                                  width=edit_region['width']*1000, height=edit_region['height']*1000,
                                  metadata=dict(type='obj', text='', desc='Region to edit: ' + prompt, palette=[]))]}}}
        instruction = ('In <ref_image_0>, apply the following edit to the selected region <obj_1>: '
                       + prompt + ' Keep the rest of the image unchanged.')
        graph['49'] = {'class_type': 'Flux3LayoutPrompt', 'inputs': {
            'prompt': instruction, 'element': ['33', 2]}}
        if filename:
            graph[generate]['inputs']['prompt'] = ['49', 0]
            graph[system]['inputs']['value'] = (
                'You are the prompt upsampler for an image-editing model. You see the original reference image. '
                'The user request includes a layout table locating the region <obj_1> in <ref_image_0>. '
                'Coordinates are [top, left, bottom, right] on a 0 to 1000 grid. '
                'Expand the requested change into one concrete English editing instruction of 50 to 150 words. '
                'Refer explicitly to <ref_image_0> and the selected region <obj_1>, preserving those exact tags. '
                'Apply only the requested change in that region; describe its appearance and relevant lighting. '
                'Do not invent extra objects or change the composition. Close with what remains unchanged. '
                'Output only prose, without JSON, coordinates, Markdown, or explanations. '
                'The layout table is appended separately after your output.')
            # Keep the exact box even if the upsampler omits its coordinates.
            graph['scenelab_region_prompt'] = {'class_type': 'Flux3LayoutPrompt', 'inputs': {
                'prompt': [generate, 0], 'element': ['33', 2]}}
            graph[encode]['inputs']['text'] = ['scenelab_region_prompt', 0]
        else:
            graph[encode]['inputs']['text'] = ['49', 0]
    graph['9']['inputs']['filename_prefix'] = 'SceneLab_Flux3_Klein' + ('_Edit' if edit else '')
    used = set()
    def visit(key):
        if key in used:
            return
        used.add(key)
        for value in graph[key]['inputs'].values():
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in graph and type(value[1]) is int:
                visit(value[0])
    visit('9')
    return {key: value for key,value in graph.items() if key in used}


def build_sdxl(prompt, width, height, seed, profile, source_name=None, denoise=0.7, mask_name=None):
    """One checkpoint supplies SDXL model, both text encoders and VAE."""
    import json
    from pathlib import Path
    root = (Path(__file__).resolve().parent / 'workflows').resolve()
    path = (root / profile['workflows']['generate']).resolve()
    if path.parent != root:
        raise ValueError('ワークフローの保存先が不正です。')
    graph = json.loads(path.read_text(encoding='utf-8'))
    graph['1']['inputs']['ckpt_name'] = profile['files']['checkpoint']
    graph['2']['inputs']['text'] = prompt
    graph['4']['inputs'].update(width=width, height=height)
    graph['5']['inputs'].update(profile['sampling'], seed=seed)
    if source_name:
        graph.pop('4')
        graph['10'] = {'class_type': 'LoadImage', 'inputs': {'image': source_name}}
        if mask_name:
            graph['11'] = {'class_type': 'LoadImage', 'inputs': {'image': mask_name}}
            graph['12'] = {'class_type': 'ImageToMask', 'inputs': {'image': ['11',0], 'channel': 'red'}}
            graph['13'] = {'class_type': 'VAEEncodeForInpaint', 'inputs': {
                'pixels': ['10',0], 'vae': ['1',2], 'mask': ['12',0], 'grow_mask_by': 8}}
        else:
            graph['13'] = {'class_type': 'VAEEncode', 'inputs': {'pixels': ['10',0], 'vae': ['1',2]}}
        graph['5']['inputs'].update(latent_image=['13',0], denoise=1.0 if mask_name else max(0.1,min(1.0,float(denoise))))
        graph['7']['inputs']['filename_prefix'] += '_Inpaint' if mask_name else '_Img2Img'
    return graph
