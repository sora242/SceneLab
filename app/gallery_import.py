"""Import browser-selected image copies without reading arbitrary server paths."""
import base64
import time

HTML = '''<div id="galleryImportModal" class="modalBackdrop"><div class="modal"><div class="modalTitle">画像をインポート</div><div class="sub">画像をギャラリーにコピーします。元ファイルは変更しません。フォルダはサブフォルダ内の画像も対象です。</div><div class="modalActions" style="display:grid;grid-template-columns:1fr 1fr"><button id="galleryImportFiles" type="button" class="mutedSmall">画像ファイルを選択</button><button id="galleryImportFolder" type="button" class="mutedSmall">フォルダを選択</button></div><div id="galleryImportResult" class="sub" style="white-space:pre-wrap;margin-top:16px" role="status"></div><div class="modalActions"><button id="galleryImportClose" class="mutedSmall" type="button">閉じる</button></div></div></div><input id="galleryImportFileInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,image/avif" multiple hidden><input id="galleryImportFolderInput" type="file" webkitdirectory multiple hidden>'''


def install_html(html):
    return html.replace('</body>', HTML + '<script src="/gallery_import.js"></script></body>', 1)


def import_image(body, bridge):
    encoded = body.get('image_data')
    if not isinstance(encoded, str) or not encoded.startswith('data:image/png;base64,'):
        raise ValueError('画像データをPNG形式で送信してください。')
    try:
        raw = base64.b64decode(encoded.split(',', 1)[1], validate=True)
    except (ValueError, TypeError) as e:
        raise ValueError('画像データを読み取れませんでした。') from e
    if len(raw) < 45 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[8:16] != b'\x00\x00\x00\rIHDR' or not raw.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise ValueError('PNG画像として読み取れませんでした。')
    width = int.from_bytes(raw[16:20], 'big')
    height = int.from_bytes(raw[20:24], 'big')
    if width < 1 or height < 1:
        raise ValueError('画像サイズが不正です。')
    filename = str(body.get('filename', 'image.png')).replace('\\', '/').rsplit('/', 1)[-1][:255]
    metadata = {'mode': 'import', 'original_filename': filename, 'user_instruction': '', 'final_prompt': '',
                'width': width, 'height': height, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    image = bridge['save_gallery_image']({'filename': filename, 'type': 'import'}, metadata, image_data=raw)
    return {'image': image}
