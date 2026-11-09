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
    graph['459:474']['inputs']['prompt'] = prompt
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
