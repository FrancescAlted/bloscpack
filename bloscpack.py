#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim :set ft=py:

""" Command line interface to Blosc via python-blosc """

from __future__ import division

import sys
import os.path as path
import argparse
import struct
import math
import zlib
import hashlib
import blosc

__version__ = '0.1.1-dev'
__author__ = 'Valentin Haenel <valentin.haenel@gmx.de>'

EXTENSION = '.blp'
MAGIC = 'blpk'
BLOSCPACK_HEADER_LENGTH = 16
BLOSC_HEADER_LENGTH = 16
FORMAT_VERSION = 1
MAX_FORMAT_VERSION = 255
MAX_CHUNKS = (2**63)-1
DEFAULT_CHUNK_SIZE = '1M'
DEFAULT_TYPESIZE = 4
DEFAULT_CLEVEL = 7
DEAFAULT_SHUFFLE = True
BLOSC_ARGS = ['typesize', 'clevel', 'shuffle']
DEFAULT_BLOSC_ARGS = dict(zip(BLOSC_ARGS,
    (DEFAULT_TYPESIZE, DEFAULT_CLEVEL, DEAFAULT_SHUFFLE)))
NORMAL  = 'NORMAL'
VERBOSE = 'VERBOSE'
DEBUG   = 'DEBUG'
LEVEL = NORMAL
VERBOSITY_LEVELS = [NORMAL, VERBOSE, DEBUG]
PREFIX = "bloscpack.py"
SUFFIXES = { "B": 1,
             "K": 2**10,
             "M": 2**20,
             "G": 2**30,
             "T": 2**40}

class Hash(object):
    """ Uniform hash object.

    Parameters
    ----------
    name : str
        the name of the hash
    size : int
        the length of the digest in bytes
    function : callable
        the hash function implementation

    Notes
    -----
    The 'function' argument should return the raw bytes as string.

    """

    def __init__(self, name, size, function):
        self.name, self.size, self._function = name, size, function

    def __call__(self, data):
        return self._function(data)

def zlib_hash(func):
    """ Wrapper for zlib hashes. """
    def hash_(data):
        # The binary OR is recommended to obtain uniform hashes on all python
        # versions and platforms. The type with be 'uint32'.
       return struct.pack('<I', func(data) & 0xffffffff)
    return 4, hash_

def hashlib_hash(func):
    """ Wrapper for hashlib hashes. """
    def hash_(data):
        return func(data).digest()
    return func().digest_size, hash_

CHECKSUMS = [Hash('None', 0, lambda data: ''),
     Hash('adler32', *zlib_hash(zlib.adler32)),
     Hash('crc32', *zlib_hash(zlib.crc32)),
     Hash('md5', *hashlib_hash(hashlib.md5)),
     Hash('sha1', *hashlib_hash(hashlib.sha1)),
     Hash('sha224', *hashlib_hash(hashlib.sha224)),
     Hash('sha256', *hashlib_hash(hashlib.sha256)),
     Hash('sha384', *hashlib_hash(hashlib.sha384)),
     Hash('sha512', *hashlib_hash(hashlib.sha512)),
    ]
CHECKSUMS_AVAIL = [c.name for c in CHECKSUMS]
CHECKSUMS_LOOKUP = dict(((c.name, c) for c in CHECKSUMS))
DEFAULT_CHECKSUM = 'adler32'

def print_verbose(message, level=VERBOSE):
    """ Print message with desired verbosity level. """
    if level not in VERBOSITY_LEVELS:
        raise TypeError("Desired level '%s' is not one of %s" % (level,
            str(VERBOSITY_LEVELS)))
    if VERBOSITY_LEVELS.index(level) <= VERBOSITY_LEVELS.index(LEVEL):
        print('%s: %s' % (PREFIX, message))

def error(message, exit_code=1):
    """ Print message and exit with desired code. """
    for line in [l for l in message.split('\n') if l != '']:
        print('%s: error: %s' % (PREFIX, line))
    sys.exit(exit_code)

def pretty_size(size_in_bytes):
    """ Pretty print filesize.  """
    for suf, lim in reversed(sorted(SUFFIXES.items(), key=lambda x: x[1])):
        if size_in_bytes < lim:
            continue
        else:
            return str(round(size_in_bytes/lim, 2))+suf

def reverse_pretty(readable):
    """ Reverse pretty printed file size. """
    # otherwise we assume it has a suffix
    suffix = readable[-1]
    if suffix not in SUFFIXES.keys():
        raise ValueError(
                "'%s' is not a valid prefix multiplier, use one of: '%s'" %
                (suffix, SUFFIXES.keys()))
    else:
        return int(float(readable[:-1]) * SUFFIXES[suffix])

class BloscPackCustomFormatter(argparse.HelpFormatter):
    """ Custom HelpFormatter.

    Basically a combination and extension of ArgumentDefaultsHelpFormatter and
    RawTextHelpFormatter. Adds default values to argument help, but only if the
    default is not in [None, True, False]. Also retains all whitespace as it
    is.

    """
    def _get_help_string(self, action):
        help_ = action.help
        if '%(default)' not in action.help \
                and action.default not in \
                [argparse.SUPPRESS, None, True, False]:
            defaulting_nargs = [argparse.OPTIONAL, argparse.ZERO_OR_MORE]
            if action.option_strings or action.nargs in defaulting_nargs:
                help_ += ' (default: %(default)s)'
        return help_

    def _split_lines(self, text, width):
        return text.splitlines()

def create_parser():
    """ Create and return the parser. """
    parser = argparse.ArgumentParser(
            #usage='%(prog)s [GLOBAL_OPTIONS] (compress | decompress)
            # [COMMAND_OPTIONS] <in_file> [<out_file>]',
            description='command line de/compression with blosc',
            formatter_class=BloscPackCustomFormatter)
    ## print version of bloscpack, python-blosc and blosc itself
    parser.add_argument('--version',
            action='version',
            version='%(prog)s:\t' + ("'%s'\n" % __version__) + \
                    "python-blosc:\t'%s'\n"   % blosc.version.__version__ + \
                    "blosc:\t\t'%s'\n"        % blosc.BLOSC_VERSION_STRING)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument('-v', '--verbose',
            action='store_true',
            default=False,
            help='be verbose about actions')
    output_group.add_argument('-d', '--debug',
            action='store_true',
            default=False,
            help='print debugging output too')
    global_group = parser.add_argument_group(title='global options')
    global_group.add_argument('-f', '--force',
            action='store_true',
            default=False,
            help='disable overwrite checks for existing files\n' + \
            '(use with caution)')
    class CheckThreadOption(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            if not 1 <= value <= blosc.BLOSC_MAX_THREADS:
                error('%s must be 1 <= n <= %d'
                        % (option_string, blosc.BLOSC_MAX_THREADS))
            setattr(namespace, self.dest, value)
    global_group.add_argument('-n', '--nthreads',
            metavar='[1, %d]' % blosc.BLOSC_MAX_THREADS,
            action=CheckThreadOption,
            default=blosc.ncores,
            type=int,
            dest='nthreads',
            help='set number of threads, (default: %(default)s (ncores))')

    subparsers = parser.add_subparsers(title='subcommands',
            metavar='', dest='subcommand')

    compress_parser = subparsers.add_parser('compress',
            formatter_class=BloscPackCustomFormatter,
            help='perform compression on file')
    c_parser = subparsers.add_parser('c',
            formatter_class=BloscPackCustomFormatter,
            help="alias for 'compress'")

    class CheckNchunksOption(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            if not 1 <= value <= MAX_CHUNKS:
                error('%s must be 1 <= n <= %d'
                        % (option_string, MAX_CHUNKS))
            setattr(namespace, self.dest, value)
    class CheckChunkSizeOption(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            try:
                # try to get the value as bytes
                value = reverse_pretty(value)
            except ValueError as ve:
                error('%s error: %s' % (option_string, ve.message))
            if value < 0:
                error('%s must be > 0 ' % option_string)
            setattr(namespace, self.dest, value)
    for p in [compress_parser, c_parser]:
        blosc_group = p.add_argument_group(title='blosc settings')
        blosc_group.add_argument('-t', '--typesize',
                metavar='<size>',
                default=DEFAULT_TYPESIZE,
                type=int,
                help='typesize for blosc')
        blosc_group.add_argument('-l', '--clevel',
                default=DEFAULT_CLEVEL,
                choices=range(10),
                metavar='[0, 9]',
                type=int,
                help='compression level')
        blosc_group.add_argument('-s', '--no-shuffle',
                action='store_false',
                default=DEAFAULT_SHUFFLE,
                dest='shuffle',
                help='deactivate shuffle')
        bloscpack_chunking_group = p.add_mutually_exclusive_group()
        bloscpack_chunking_group.add_argument('-c', '--nchunks',
                metavar='[1, 2**32-1]',
                action=CheckNchunksOption,
                type=int,
                default=None,
                help='set desired number of chunks')
        bloscpack_chunking_group.add_argument('-z', '--chunk-size',
                metavar='<size>',
                action=CheckChunkSizeOption,
                type=str,
                default=None,
                dest='chunk_size',
                help='set desired chunk size (default: %s)' %
                DEFAULT_CHUNK_SIZE)
        bloscpack_group = p.add_argument_group(title='bloscpack settings')
        def join_with_eol(items):
            return ', '.join(items) + '\n'
        checksum_format = join_with_eol(CHECKSUMS_AVAIL[0:3]) + \
                join_with_eol(CHECKSUMS_AVAIL[3:6]) + \
                join_with_eol(CHECKSUMS_AVAIL[6:])
        checksum_help='set desired checksum:\n' + checksum_format
        bloscpack_group.add_argument('-k', '--checksum',
                metavar='<checksum>',
                type=str,
                choices=CHECKSUMS_AVAIL,
                default=DEFAULT_CHECKSUM,
                dest='checksum',
                help=checksum_help)

    decompress_parser = subparsers.add_parser('decompress',
            formatter_class=BloscPackCustomFormatter,
            help='perform decompression on file')

    d_parser = subparsers.add_parser('d',
            formatter_class=BloscPackCustomFormatter,
            help="alias for 'decompress'")

    for p in [decompress_parser, d_parser]:
        p.add_argument('-e', '--no-check-extension',
                action='store_true',
                default=False,
                dest='no_check_extension',
                help='disable checking input file for extension (*.blp)\n' +
                '(requires use of <out_file>)')

    for p, help_in, help_out in [(compress_parser,
            'file to be compressed', 'file to compress to'),
                                 (c_parser,
            'file to be compressed', 'file to compress to'),
                                 (decompress_parser,
            'file to be decompressed', 'file to decompress to'),
                                 (d_parser,
            'file to be decompressed', 'file to decompress to'),
                                  ]:
        p.add_argument('in_file',
                metavar='<in_file>',
                type=str,
                default=None,
                help=help_in)
        p.add_argument('out_file',
                metavar='<out_file>',
                type=str,
                nargs='?',
                default=None,
                help=help_out)

    return parser

def decode_blosc_header(buffer_):
    """ Read and decode header from compressed Blosc buffer.

    Parameters
    ----------
    buffer_ : string of bytes
        the compressed buffer

    Returns
    -------
    settings : dict
        a dict containing the settings from Blosc

    Notes
    -----

    The Blosc 1.1.3 header is 16 bytes as follows:

    |-0-|-1-|-2-|-3-|-4-|-5-|-6-|-7-|-8-|-9-|-A-|-B-|-C-|-D-|-E-|-F-|
      ^   ^   ^   ^ |     nbytes    |   blocksize   |    ctbytes    |
      |   |   |   |
      |   |   |   +--typesize
      |   |   +------flags
      |   +----------versionlz
      +--------------version

    The first four are simply bytes, the last three are are each unsigned ints
    (uint32) each occupying 4 bytes. The header is always little-endian.
    'ctbytes' is the length of the buffer including header and nbytes is the
    length of the data when uncompressed.

    """
    def decode_byte(byte):
        return int(byte.encode('hex'), 16)
    def decode_uint32(fourbyte):
        return struct.unpack('<I', fourbyte)[0]
    return {'version': decode_byte(buffer_[0]),
            'versionlz': decode_byte(buffer_[1]),
            'flags': decode_byte(buffer_[2]),
            'typesize': decode_byte(buffer_[3]),
            'nbytes': decode_uint32(buffer_[4:8]),
            'blocksize': decode_uint32(buffer_[8:12]),
            'ctbytes': decode_uint32(buffer_[12:16])}

class ChunkingException(BaseException):
    pass

class NoSuchChecksum(ValueError):
    pass

def calculate_nchunks(in_file_size, nchunks=None, chunk_size=None):
    """ Determine chunking for an input file.

    Parameters
    ----------
    in_file_size : int
        the size of the input file
    nchunks : int, default: None
        the number of chunks desired by the user
    chunk_size : int, default: None
        the desired chunk size

    Returns
    -------
    nchunks, chunk_size, last_chunk_size

    nchunks : int
        the number of chunks
    chunk_size : int
        the size of each chunk in bytes
    last_chunk_size : int
        the size of the last chunk in bytes

    Raises
    ------
    ChunkingException
        under various error conditions

    Notes
    -----
    If both 'nchunks' 'chunk_size' are 'None' the chunk_size is set to be
    'blosc.BLOSC_MAX_BUFFERSIZE'.

    """
    if nchunks is not None and chunk_size is not None:
        raise ValueError(
                "either specify 'nchunks' or 'chunk_size', but not both")
    elif nchunks is not None and chunk_size is None:
        print_verbose("'nchunks' proposed", level=DEBUG)
        if nchunks > in_file_size:
            raise ChunkingException(
                    "Your value of 'nchunks': %d is " % nchunks +
                    "greater than the 'in_file size': %d" % in_file_size)
        elif nchunks <= 0:
            raise ChunkingException(
                    "'nchunks' must be greate than zero, not '%d' " % nchunks)
        quotient, remainder = divmod(in_file_size, nchunks)
        if nchunks == 1:
            chunk_size = 0
            last_chunk_size = in_file_size
        elif remainder == 0:
            chunk_size = quotient
            last_chunk_size = chunk_size
        elif nchunks == 2:
            chunk_size = quotient
            last_chunk_size = in_file_size - chunk_size
        else:
            chunk_size = in_file_size//(nchunks-1)
            last_chunk_size = in_file_size - chunk_size * (nchunks-1)
    elif nchunks is None and chunk_size is not None:
        print_verbose("'chunk_size' proposed", level=DEBUG)
        if chunk_size > in_file_size:
            raise ChunkingException(
                    "Your value of 'chunk_size': %d is " % chunk_size +
                    "greater than the 'in_file size': %d" % in_file_size)
        elif chunk_size <= 0:
            raise ChunkingException(
                    "'chunk_size' must be greate than zero, not '%d' " %
                    chunk_size)
        quotient, remainder = divmod(in_file_size, chunk_size)
        if chunk_size == in_file_size:
            nchunks = 1
            chunk_size = 0
            last_chunk_size = in_file_size
        elif remainder == 0:
            nchunks = quotient
            last_chunk_size = chunk_size
        else:
            nchunks = quotient + 1
            last_chunk_size = remainder
    elif nchunks is None and chunk_size is None:
        nchunks =  int(math.ceil(in_file_size/blosc.BLOSC_MAX_BUFFERSIZE))
        quotient, remainder = divmod(in_file_size, blosc.BLOSC_MAX_BUFFERSIZE)
        if in_file_size == blosc.BLOSC_MAX_BUFFERSIZE:
            nchunks = 1
            chunk_size = 0
            last_chunk_size = blosc.BLOSC_MAX_BUFFERSIZE
        elif quotient == 0:
            chunk_size = 0
            last_chunk_size = in_file_size
        else:
            chunk_size = blosc.BLOSC_MAX_BUFFERSIZE
            last_chunk_size = in_file_size % blosc.BLOSC_MAX_BUFFERSIZE
    if chunk_size > blosc.BLOSC_MAX_BUFFERSIZE \
            or last_chunk_size > blosc.BLOSC_MAX_BUFFERSIZE:
        raise ChunkingException(
            "Your value of 'nchunks' would lead to chunk sizes bigger than " +\
            "'BLOSC_MAX_BUFFERSIZE', please use something smaller.\n" +\
            "nchunks : %d\n" % nchunks +\
            "chunk_size : %d\n" % chunk_size +\
            "last_chunk_size : %d\n" % last_chunk_size +\
            "BLOSC_MAX_BUFFERSIZE : %d\n" % blosc.BLOSC_MAX_BUFFERSIZE)
    elif nchunks > MAX_CHUNKS:
        raise ChunkingException(
                "nchunks: '%d' is greater than the MAX_CHUNKS: '%d'" %
                (nchunks, MAX_CHUNKS))
    print_verbose('nchunks: %d' % nchunks, level=VERBOSE)
    print_verbose('chunk_size: %s' % pretty_size(chunk_size), level=VERBOSE)
    print_verbose('last_chunk_size: %s' % pretty_size(last_chunk_size),
            level=DEBUG)
    return nchunks, chunk_size, last_chunk_size

def create_bloscpack_header(nchunks=None, format_version=FORMAT_VERSION):
    """ Create the bloscpack header string.

    Parameters
    ----------
    nchunks : int
        the number of chunks, default: None
    format_version : int
        the version format for the compressed file

    Returns
    -------
    bloscpack_header : string
        the header as string

    Notes
    -----

    The bloscpack header is 16 bytes as follows:

    |-0-|-1-|-2-|-3-|-4-|-5-|-6-|-7-|-8-|-9-|-A-|-B-|-C-|-D-|-E-|-F-|
    | b   l   p   k | ^ | RESERVED  |           nchunks             |
                   version

    The first four are the magic string 'blpk'. The next one is an 8 bit
    unsigned little-endian integer that encodes the format version. The next
    three are reserved, and the last eight are a signed  64 bit little endian
    integer that encodes the number of chunks

    The value of '-1' for 'nchunks' designates an unknown size and can be
    inserted by setting 'nchunks' to None.

    Raises
    ------
    ValueError
        if the nchunks argument is too large or negative
    struct.error
        if the format_version is too large or negative

    """
    if not 0 <= nchunks <= MAX_CHUNKS and nchunks is not None:
        raise ValueError(
                "'nchunks' must be in the range 0 <= n <= %d, not '%s'" %
                (MAX_CHUNKS, str(nchunks)))
    return (MAGIC + struct.pack('<B', format_version) + '\x00\x00\x00' +
            struct.pack('<q', nchunks if nchunks is not None else -1))

def decode_bloscpack_header(buffer_):
    """ Check that the magic marker exists and return number of chunks. 

    Parameters
    ----------
    buffer_ : str (but probably any sequence would work)A
        the buffer_

    Returns
    -------
    nchunks : int
        the number of chunks in the file, or -1 if unknowen
    format_version : int
        the format version of the file

    """
    if len(buffer_) != 16:
        raise ValueError(
            "attempting to decode a bloscpack header of length '%d', not '16'"
            % len(buffer_))
    elif buffer_[0:4] != MAGIC:
        raise ValueError(
            "the magic marker '%s' is missing from the bloscpack " % MAGIC +
            "header, instead we found: '%s'" % buffer_[0:4])
    return (struct.unpack('<q', buffer_[8:16])[0],
                struct.unpack('<I', buffer_[4:8])[0])

def process_compression_args(args):
    """ Extract and check the compression args after parsing by argparse.

    Parameters
    ----------
    args : argparse.Namespace
        the parsed command line arguments

    Returns
    -------
    in_file : str
        the input file name
    out_file : str
        the out_file name
    blosc_args : tuple of (int, int, bool)
        typesize, clevel and shuffle
    """
    in_file = args.in_file
    out_file = in_file + EXTENSION \
        if args.out_file is None else args.out_file
    blosc_args = dict((arg, args.__getattribute__(arg)) for arg in BLOSC_ARGS)
    return in_file, out_file, blosc_args

def process_decompression_args(args):
    """ Extract and check the decompression args after parsing by argparse.

    Warning: may call sys.exit()

    Parameters
    ----------
    args : argparse.Namespace
        the parsed command line arguments

    Returns
    -------
    in_file : str
        the input file name
    out_file : str
        the out_file name
    """
    in_file = args.in_file
    out_file = args.out_file
    # remove the extension for output file
    if args.no_check_extension:
        if out_file is None:
            error('--no-check-extension requires use of <out_file>')
    else:
        if in_file.endswith(EXTENSION):
            out_file = in_file[:-len(EXTENSION)] \
                    if args.out_file is None else args.out_file
        else:
            error("input file '%s' does not end with '%s'" %
                    (in_file, EXTENSION))
    return in_file, out_file

def check_files(in_file, out_file, args):
    """ Check files exist/don't exist.

    Warning: may call sys.exit()

    """
    if not path.exists(in_file):
        error("input file '%s' does not exist!" % in_file)
    if path.exists(out_file):
        if not args.force:
            error("output file '%s' exists!" % out_file)
        else:
            print_verbose("overwriting existing file: %s" % out_file)
    print_verbose('input file is: %s' % in_file)
    print_verbose('output file is: %s' % out_file)

def process_nthread_arg(args):
    """ Extract and set nthreads. """
    if args.nthreads != blosc.ncores:
        blosc.set_nthreads(args.nthreads)
    print_verbose('using %d thread%s' %
            (args.nthreads, 's' if args.nthreads > 1 else ''))

def pack_file(in_file, out_file, blosc_args, nchunks=None, chunk_size=None):
    """ Main function for compressing a file.

    Parameters
    ----------
    in_file : str
        the name of the input file
    out_file : str
        the name of the output file
    blosc_args : dict
        dictionary of blosc keyword args
    nchunks : int, default: None
        The desired number of chunks.
    chunk_size : int, default: None
        The desired chunk size in bytes.

    Notes
    -----
    The parameters 'nchunks' and 'chunk_size' are mutually exclusive. Will be
    determined automatically if not present.

    """
    # calculate chunk sizes
    in_file_size = path.getsize(in_file)
    print_verbose('input file size: %s' % pretty_size(in_file_size))
    nchunks, chunk_size, last_chunk_size = \
            calculate_nchunks(in_file_size, nchunks, chunk_size)
    # calculate header
    bloscpack_header = create_bloscpack_header(nchunks)
    print_verbose('bloscpack_header: %s' % repr(bloscpack_header), level=DEBUG)
    # write the chunks to the file
    with open(in_file, 'rb') as input_fp, \
         open(out_file, 'wb') as output_fp:
        output_fp.write(bloscpack_header)
        # if nchunks == 1 the last_chunk_size is the size of the single chunk
        for i, bytes_to_read in enumerate((
                [chunk_size] * (nchunks - 1)) + [last_chunk_size]):
            current_chunk = input_fp.read(bytes_to_read)
            compressed = blosc.compress(current_chunk, **blosc_args)
            output_fp.write(compressed)
            print_verbose("chunk '%d'%s written, in: %s out: %s" %
                    (i, ' (last)' if i == nchunks-1 else '',
                    pretty_size(len(current_chunk)),
                    pretty_size(len(compressed))),
                    level=DEBUG)
    out_file_size = path.getsize(out_file)
    print_verbose('output file size: %s' % pretty_size(out_file_size))
    print_verbose('compression ratio: %f' % (out_file_size/in_file_size))

def unpack_file(in_file, out_file):
    """ Main function for decompressing a file.

    Parameters
    ----------
    in_file : str
        the name of the input file
    out_file : str
        the name of the output file
    """
    in_file_size = path.getsize(in_file)
    print_verbose('input file size: %s' % pretty_size(in_file_size))
    with open(in_file, 'rb') as input_fp, \
         open(out_file, 'wb') as output_fp:
        # read the bloscpack header
        print_verbose('reading bloscpack header', level=DEBUG)
        bloscpack_header = input_fp.read(BLOSCPACK_HEADER_LENGTH)
        nchunks, format_version = decode_bloscpack_header(bloscpack_header)
        print_verbose('nchunks: %d, format_version: %d' %
                (nchunks, format_version), level=DEBUG)
        if FORMAT_VERSION != format_version:
            error("format version of file was not '%s' as expected, but '%d'" %
                    (FORMAT_VERSION, format_version))
        for i in range(nchunks):
            print_verbose("decompressing chunk '%d'%s" %
                    (i, ' (last)' if i == nchunks-1 else ''), level=DEBUG)
            blosc_header_raw = input_fp.read(BLOSC_HEADER_LENGTH)
            blosc_header = decode_blosc_header(blosc_header_raw)
            print_verbose('blosc_header: %s' % repr(blosc_header), level=DEBUG)
            ctbytes = blosc_header['ctbytes']
            # seek back BLOSC_HEADER_LENGTH bytes in file relative to current
            # position
            input_fp.seek(-BLOSC_HEADER_LENGTH, 1)
            compressed = input_fp.read(ctbytes)
            decompressed = blosc.decompress(compressed)
            output_fp.write(decompressed)
            print_verbose("chunk written, in: %s out: %s" %
                    (pretty_size(len(compressed)),
                        pretty_size(len(decompressed))), level=DEBUG)
    out_file_size = path.getsize(out_file)
    print_verbose('output file size: %s' % pretty_size(out_file_size))
    print_verbose('decompression ratio: %f' % (out_file_size/in_file_size))

if __name__ == '__main__':
    parser = create_parser()
    PREFIX = parser.prog
    args = parser.parse_args()
    if args.verbose:
        LEVEL = VERBOSE
    elif args.debug:
        LEVEL = DEBUG
    print_verbose('command line argument parsing complete', level=DEBUG)
    print_verbose('command line arguments are: ', level=DEBUG)
    for arg, val in vars(args).iteritems():
        print_verbose('\t%s: %s' % (arg, str(val)), level=DEBUG)

    # compression and decompression handled via subparsers
    if args.subcommand in ['compress', 'c']:
        print_verbose('getting ready for compression')
        in_file, out_file, blosc_args = process_compression_args(args)
        print_verbose('blosc args are:', level=DEBUG)
        for arg, value in blosc_args.iteritems():
            print_verbose('\t%s: %s' % (arg, value), level=DEBUG)
        check_files(in_file, out_file, args)
        process_nthread_arg(args)
        # mutually exclusivity in parser protects us from both having a value
        if args.nchunks is None and args.chunk_size is None:
            args.chunk_size = reverse_pretty(DEFAULT_CHUNK_SIZE)
        try:
            pack_file(in_file, out_file, blosc_args,
                    nchunks=args.nchunks, chunk_size=args.chunk_size)
        except ChunkingException as e:
            error(e.message)
    elif args.subcommand in ['decompress', 'd']:
        print_verbose('getting ready for decompression')
        in_file, out_file = process_decompression_args(args)
        check_files(in_file, out_file, args)
        process_nthread_arg(args)
        try:
            unpack_file(in_file, out_file)
        except ValueError as ve:
            error(ve.message)
    else:
        # we should never reach this
        error('You found the easter-egg, please contact the author')
    print_verbose('done')
