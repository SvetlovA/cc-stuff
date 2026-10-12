#!/usr/bin/env python3
"""Check shared plugin identity, marketplace paths, and packaged resources."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def contained_path(root, value):
    assert isinstance(value, str) and value.startswith('./'), value
    assert '..' not in Path(value).parts, value
    path = (root / value).resolve()
    assert path.is_relative_to(root.resolve()) and path.exists(), path
    return path


def validate(root=ROOT):
    claude = read_json(root / '.claude-plugin/marketplace.json')
    codex = read_json(root / '.agents/plugins/marketplace.json')
    assert claude['name'] == codex['name'], 'Marketplace names differ'
    entries = {}
    for host, registry in [('claude', claude), ('codex', codex)]:
        entries[host] = {}
        for item in registry['plugins']:
            name = item['name']
            assert name not in entries[host], f'Duplicate plugin: {name}'
            source = item['source']
            if host == 'codex':
                assert source['source'] == 'local', source
                assert item['policy']['installation'] == 'AVAILABLE'
                assert item['policy']['authentication'] == 'ON_INSTALL'
                assert item['category']
                source = source['path']
            entries[host][name] = contained_path(root, source)
    assert entries['claude'] == entries['codex'], 'Marketplace plugins/paths differ'
    # Detect unregistered top-level plugins as well as stale registry entries.
    packaged = {p.parent.parent for host in ('claude', 'codex')
                for p in root.glob(f'*/.{host}-plugin/plugin.json')}
    assert packaged == set(entries['claude'].values()), 'Unregistered plugin'
    count = 0
    for name, plugin in entries['claude'].items():
        cm = read_json(plugin / '.claude-plugin/plugin.json')
        xm = read_json(plugin / '.codex-plugin/plugin.json')
        for key in ('name', 'version', 'description', 'author', 'homepage',
                    'repository', 'license', 'keywords'):
            assert cm.get(key) == xm.get(key), f'{name}: {key} differs'
        assert cm['name'] == name
        assert len(cm['version'].split('.')) == 3
        skills = contained_path(plugin, xm['skills'])
        skill_files = sorted(skills.glob('*/SKILL.md'))
        assert skill_files, f'{name}: no skills'
        for skill in skill_files:
            text = skill.read_text()
            assert text.startswith('---\n'), skill
            frontmatter = text.split('---', 2)[1]
            assert f'\nname: {skill.parent.name}\n' in frontmatter, skill
            assert '\ndescription:' in frontmatter, skill
            count += 1
        hooks = plugin / 'hooks/hooks.json'
        if hooks.exists():
            assert contained_path(plugin, xm['hooks']) == hooks.resolve()
            for event in read_json(hooks)['hooks'].values():
                for group in event:
                    for hook in group['hooks']:
                        assert hook['type'] == 'command'
                        assert '${CLAUDE_PLUGIN_ROOT}' in hook['command']
                        script = hook['command'].split('${CLAUDE_PLUGIN_ROOT}/', 1)[1].split('"', 1)[0]
                        contained_path(plugin, './' + script)
    return len(entries['claude']), count


if __name__ == '__main__':
    plugins, skills = validate()
    print(f'Validated {plugins} plugins and {skills} shared skills for both hosts.')
