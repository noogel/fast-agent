"""官方 MCP Python SDK 1.x 示例：检查当前工作区固定文件，不接受任意路径。"""
from pathlib import Path
from mcp.server.fastmcp import FastMCP

server = FastMCP('Fast Agent repository inspector')


@server.tool()
def inspect_source() -> dict:
    """检查 math_utils.py 的 add 实现是否仍错误使用减法。"""
    source = Path('math_utils.py').read_text(encoding='utf-8')
    return {'filename':'math_utils.py', 'contains_subtraction':'return a - b' in source}


if __name__ == '__main__':
    server.run(transport='stdio')
