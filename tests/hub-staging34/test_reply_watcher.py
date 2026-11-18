import ast, base64, binascii, json, re, unittest
from pathlib import Path
source = Path(__file__).with_name('room_watch.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {'strip_reply_metadata', 'resolve_mention', 'parse_template_command'}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names or isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == '_REPLY_SUFFIX' for x in n.targets)]
ns = {'re': re, 'json': json, 'base64': base64, 'binascii': binascii}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<watcher>', 'exec'), ns)
def wire(body, author='成员🦊', text='旧话 @other'):
    encoded = base64.urlsafe_b64encode(json.dumps({'author': author, 'text': text}, ensure_ascii=False).encode()).decode().rstrip('=')
    return body + '\n\n[pack-reply-v1:' + encoded + ']'
class ReplyTests(unittest.TestCase):
    def test_plain_unchanged(self):
        for value in ('普通消息', '@absent 查一下', '正文\n\n[pack-reply-v1:bad]'):
            self.assertEqual(ns['strip_reply_metadata'](value), value)
    def test_snapshot_and_mention(self):
        body = ns['strip_reply_metadata'](wire('@absent 查一下'))
        self.assertEqual(body, '@absent 查一下')
        self.assertEqual(ns['resolve_mention'](body, ['absent','other'])[:2], ('hit','absent'))
        span = ns['resolve_mention'](body, ['absent','other'])[2]
        cleaned = re.sub(r'\s+', ' ', (body[:span[0]] + body[span[1]:]).strip())
        self.assertEqual(cleaned[:40], '查一下')
    def test_snapshot_mention_does_not_dispatch(self):
        self.assertEqual(ns['resolve_mention'](ns['strip_reply_metadata'](wire('普通回复')), ['absent','other'])[0], 'none')
    def test_template_args_clean(self):
        body = ns['strip_reply_metadata'](wire('模板：例子 参数'))
        self.assertEqual(ns['parse_template_command'](body, ['例子']), ('hit','例子','参数'))
    def test_bad_json_fields_preserved(self):
        token = base64.urlsafe_b64encode(b'{"author":"","text":"x"}').decode().rstrip('=')
        value = '文字\n\n[pack-reply-v1:' + token + ']'
        self.assertEqual(ns['strip_reply_metadata'](value), value)
    def test_only_footer_stripped(self):
        value = wire('文本') + ' 后续正文'
        self.assertEqual(ns['strip_reply_metadata'](value), value)
if __name__ == '__main__': unittest.main(verbosity=2)
