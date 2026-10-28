"""Bundled vendor marks: catalog data cannot inject remote images or SVG code."""
VENDORS = {
    'zai': 'Z.ai / 智谱', 'qwen': 'Qwen / 通义千问', 'minimax': 'MiniMax',
    'deepseek': 'DeepSeek', 'openai': 'OpenAI', 'anthropic': 'Anthropic',
    'google': 'Google', 'generic': '厂商图标待配置',
}


def vendor_info(model_id, vendor=None):
    if vendor not in VENDORS:
        name = model_id.casefold()
        vendor = next((key for prefixes, key in (
            (('glm', 'zai', 'zhipu'), 'zai'), (('qwen', 'qwq'), 'qwen'),
            (('minimax', 'h3'), 'minimax'), (('deepseek',), 'deepseek'),
            (('gpt', 'o1-', 'o3-', 'o4-', 'openai'), 'openai'),
            (('claude', 'anthropic'), 'anthropic'), (('gemini', 'gemma'), 'google'),
        ) if name.startswith(prefixes)), 'generic')
    return {'vendor': vendor, 'vendor_name': VENDORS[vendor],
            'vendor_logo': '/model-logos/'+vendor+'.svg'}
