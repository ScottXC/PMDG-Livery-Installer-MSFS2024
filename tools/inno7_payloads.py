"""Read-only inspection of this project's unencrypted Inno 7/LZMA2 builds.

Format reference: https://github.com/jrsoftware/issrc/tree/main/Projects/Src
Shared.Struct.pas, Compression.Base.pas and Setup.FileExtractor.pas.
This is intentionally narrow: other formats fail instead of being declared clean.
No installer is executed, and no files or registry entries are installed.
"""
import hashlib
import lzma
import struct
import zlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def undo_calls(data, block_size):
    """Reverse Inno's v3 relative CALL/JMP filter, using its block boundaries."""
    result = bytearray(data)
    for start in range(0, len(result), block_size):
        index, end = start, min(start + block_size, len(result)) - 4
        while index < end:
            if result[index] not in (0xe8, 0xe9):
                index += 1
                continue
            index += 1
            if result[index + 3] in (0, 255):
                relative = (int.from_bytes(result[index:index + 3], "little") - index - 4) & 0xffffff
                if relative & 0x800000:
                    result[index + 3] ^= 255
                result[index:index + 3] = relative.to_bytes(3, "little")
            index += 4
    return bytes(result)


def compressed_block(data, offset):
    crc, size, compressed = struct.unpack_from("<IQB", data, offset)
    require(zlib.crc32(data[offset + 4:offset + 13]) == crc, "Inno block header CRC mismatch")
    offset += 13
    end = offset + size
    require(0 <= size <= len(data) - offset, "Invalid Inno block length")
    packed = bytearray()
    while offset < end:
        crc = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        part = data[offset:min(offset + 4096, end)]
        require(bool(part) and zlib.crc32(part) == crc, "Inno block data CRC mismatch")
        packed.extend(part)
        offset += len(part)
    if compressed:
        packed = lzma.decompress(bytes(packed[:5]) + b"\xff" * 8 + bytes(packed[5:]), format=lzma.FORMAT_ALONE)
    return bytes(packed), end


def inspect(data):
    marker = b"rDlPtS\xcd\xe6\xd7{\x0b*"
    offset = data.index(marker)
    fields = struct.unpack_from("<12sIqqIIqqII", data, offset)
    _, version, total, runtime_offset, runtime_size, runtime_crc, header_offset, files_offset, _, crc = fields
    require(version == 2 and total == len(data), "Unsupported Inno offset table")
    require(zlib.crc32(data[offset:offset + 60]) == crc, "Inno offset table CRC mismatch")
    require(data[header_offset:header_offset + 64].rstrip(b"\0") == b"Inno Setup Setup Data (7.0.0.3)",
            "Unsupported Inno data version")
    encryption = header_offset + 64
    require(zlib.crc32(data[encryption + 4:encryption + 53]) == struct.unpack_from("<I", data, encryption)[0],
            "Inno encryption header CRC mismatch")
    require(data[encryption + 4] == 0, "Encrypted installers cannot be audited")
    header, location_offset = compressed_block(data, encryption + 53)
    locations, end = compressed_block(data, location_offset)
    require(end == runtime_offset, "Unaccounted Inno header bytes")
    runtime, end = compressed_block(data, runtime_offset)
    runtime = undo_calls(runtime, max(len(runtime), 1))
    require(end == len(data) and len(runtime) == runtime_size and zlib.crc32(runtime) == runtime_crc,
            "Embedded Inno runtime verification failed")
    record = struct.Struct("<iiqqqq32sQIIB")
    require(len(locations) % record.size == 0, "Invalid Inno file location records")
    chunks, payloads, ranges = {}, [], []
    for offset in range(0, len(locations), record.size):
        first, last, start, suboffset, size, packed_size, sha, _, _, _, flags = record.unpack_from(locations, offset)
        require(first == last == 0 and not flags & 8 and flags & 16, "Unsupported Inno payload encoding")
        position = files_offset + start
        require(data[position:position + 4] == b"zlb\x1a", "Invalid Inno payload marker")
        require(0 <= packed_size <= header_offset - position - 4, "Invalid Inno payload length")
        if start not in chunks:
            packed = data[position + 4:position + 4 + packed_size]
            prop = packed[0]
            require(prop <= 30, "Unsupported LZMA2 dictionary size")
            dictionary = (2 | (prop & 1)) << (prop // 2 + 11)
            chunks[start] = lzma.decompress(packed[1:], format=lzma.FORMAT_RAW,
                                           filters=[{"id": lzma.FILTER_LZMA2, "dict_size": dictionary}])
            ranges.append((position, position + 4 + packed_size))
        require(0 <= suboffset <= len(chunks[start]) - size and size >= 0, "Invalid Inno file bounds")
        payload = chunks[start][suboffset:suboffset + size]
        if flags & 4:
            payload = undo_calls(payload, 65536)
        require(hashlib.sha256(payload).digest() == sha, "Inno file SHA-256 mismatch")
        payloads.append(payload)
    require(ranges and sorted(ranges)[0][0] == files_offset and sorted(ranges)[-1][1] == header_offset,
            "Unaccounted Inno payload bytes")
    return header, runtime, payloads
