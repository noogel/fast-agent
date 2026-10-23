"""显式附加 CA；保留系统根证书、证书校验与主机名校验。"""
import os
import ssl
import stat


CA_LIMIT = 1024 * 1024


def tls_context(spec):
    if 'ca_file' not in spec:
        return None
    # NOFOLLOW拒绝末级链接；NONBLOCK避免替换成FIFO后阻塞在open。
    try:
        fd = os.open(spec['ca_file'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('MCP CA 文件必须是最多1MiB的普通文件') from exc
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > CA_LIMIT:
            raise ValueError('MCP CA 文件必须是最多1MiB的普通文件')
        data = stream.read(CA_LIMIT + 1)
        if len(data) > CA_LIMIT:
            raise ValueError('MCP CA 文件必须是最多1MiB的普通文件')
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=data.decode('ascii'))
    return context
