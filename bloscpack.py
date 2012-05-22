#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim :set ft=py:

""" Command line interface to Blosc via python-blosc """

from __future__ import division

import os.path as path
import argparse
import struct
import math
import blosc

__version__ = '0.1.0-dev'
__author__ = 'Valentin Haenel <valentin.haenel@gmx.de>'

EXTENSION = '.blp'
MAGIC = 'blpk'
VERBOSE = False
PREFIX = ""
BLOSC_ARGS = ['typesize', 'clevel', 'shuffle']

def print_verbose(message):
    if VERBOSE:
        print('%s: %s' % (PREFIX, message))

def pretty_size(size_in_bytes):
    """ Pretty print filesize

    From: http://www.dzone.com/snippets/filesize-nice-units

    """
    suffixes = [("B", 2**10),
                ("K", 2**20),
                ("M", 2**30),
                ("G", 2**40),
                ("T", 2**50)]
    for suf, lim in suffixes:
        if size_in_bytes > lim:
            continue
        else:
            return round(size_in_bytes/float(lim/2**10), 2).__str__()+suf

class BloscPackCustomFormatter(argparse.HelpFormatter):
    """ Custom HelpFormatter.

    Basically a combination and extension of ArgumentDefaultsHelpFormatter adn
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
    global_group = parser.add_argument_group(title='global options')
    global_group.add_argument('--verbose',
            action='store_true',
            default=False,
            help='be verbose about actions')
    global_group.add_argument('--force',
            action='store_true',
            default=False,
            help='disable overwrite checks for existing files\n' + \
            '(use with caution)')
    class CheckThreadOption(argparse.Action):
        def __call__(self, parser, namespace, value, option_string=None):
            if not 1 <= value <= blosc.BLOSC_MAX_THREADS:
                parser.error('%s must be 1 <= n <= %d'
                        % (option_string, blosc.BLOSC_MAX_THREADS))
            setattr(namespace, self.dest, value)
    global_group.add_argument('--nthreads',
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
    group = compress_parser.add_argument_group(title='blosc settings')
    group.add_argument('--typesize',
            metavar='<size>',
            default=4,
            type=int,
            help='typesize for blosc')
    group.add_argument('--clevel',
            default=7,
            choices=range(10),
            metavar='[0, 9]',
            type=int,
            help='compression level')
    group.add_argument('--no-shuffle',
            action='store_false',
            default=True,
            dest='shuffle',
            help='deactivate shuffle')

    decompress_parser = subparsers.add_parser('decompress',
            formatter_class=BloscPackCustomFormatter,
            help='perform decompression on file')

    decompress_parser.add_argument('--no-check-extension',
            action='store_true',
            default=False,
            dest='no_check_extension',
            help='disable checking input file for extension (*.blp)\n' +
            '(requires use of <out_file>)')

    for p, help_in, help_out in [(compress_parser,
            'file to be compressed', 'file to compress to'),
                                 (decompress_parser,
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

def read_blosc_header(buffer_):
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

def calculate_nchunks(in_file_size, nchunks=None):
    """ Determine chunking for an input file.

    Parameters
    ----------

    in_file_size : int
        the size of the input file
    nchunks : int
        the number of chunks desired by the user

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
        if the chunk_size resulting from nchunks makes the chunks larger than
        permitted by BLOSC_MAX_BUFFERSIZE

    """
    nchunks =  int(math.ceil(in_file_size/blosc.BLOSC_MAX_BUFFERSIZE)) \
            if nchunks is None else nchunks
    chunk_size = in_file_size//nchunks
    last_chunk_size = chunk_size + in_file_size % nchunks
    if chunk_size > blosc.BLOSC_MAX_BUFFERSIZE \
            or last_chunk_size > blosc.BLOSC_MAX_BUFFERSIZE:
        raise ChunkingException(
            "Your value of 'nchunks' would lead to chunk sizes bigger than " +\
            "'BLOSC_MAX_BUFFERSIZE', please use something smaller.\n" +\
            "proposed nchunks : %d\n" % nchunks +\
            "chunk_size : %d\n" % chunk_size +\
            "last_chunk_size : %d\n" % last_chunk_size +\
            "BLOSC_MAX_BUFFERSIZE : %d\n" % blosc.BLOSC_MAX_BUFFERSIZE)
    return nchunks, chunk_size, last_chunk_size

def create_bloscpack_header(nchunks):
    """ Create the bloscpack header string

    Parameters
    ----------
    nchunks : int
        the number of chunks

    Returns
    -------
    bloscpack_header : string
        the header as string

    Notes
    -----

    The bloscpack header is 8 bytes as follows:

    |-0-|-1-|-2-|-3-|-4-|-5-|-6-|-7-|
    | b   l   p   k |    nchunks    |

    The first four are the magic string 'blpk' and the second four are an
    unsigned 32 bit little-endian integer.

    """
    # this will fail if nchunks is larger than the max of an unsigned int
    return (MAGIC + struct.pack('<I', nchunks))

def decode_bloscpack_header(buffer_, error_func):
    # buffer should be of length 16
    if len(buffer_) != 8:
        error_func(
            'attempting to decode a bloscpack header of length other than 16')
    elif buffer_[0:4] != MAGIC:
        error_func('the magic marker is missing from the bloscpack header')
    return struct.unpack('<I', buffer_[4:])[0]

def process_compression_args(args, error_func):
    """

    Parameters
    ----------

    args : argparse.Namespace
        the parsed command line arguments
    error_func : function
        an error function that takes a single message string as argument

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

def process_decompression_args(args, error_func):
    """

    Parameters
    ----------

    args : argparse.Namespace
        the parsed command line arguments
    error_func : function
        an error function that takes a single message string as argument

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
    if args.no_check_extension and out_file is None:
        error_func('--no-check-extension requires use of <out_file>')
    else:
        if in_file.endswith(EXTENSION):
            out_file = in_file[:-len(EXTENSION)] \
                    if args.out_file is None else args.out_file
        else:
            error_func("input file '%s' does not end with '%s'" %
                    (in_file, EXTENSION))
    return in_file, out_file

def check_files(in_file, out_file, args, error_func):
    """ Check files exist/don't exist. """
    if not path.exists(in_file):
        error_func("input file '%s' does not exist!" % in_file)
    if path.exists(out_file):
        if not args.force:
            error_func("output file '%s' exists!" % out_file)
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

def pack_file(in_file, out_file, blosc_args, nchunks=None):
    # calculate chunk sizes
    in_file_size = path.getsize(in_file)
    print_verbose('Input file size: %s' % pretty_size(in_file_size))
    try:
        nchunks, chunk_size, last_chunk_size = \
            calculate_nchunks(in_file_size, nchunks)
    except ChunkingException as e:
        # TODO print e.message
        pass
    print_verbose('nchunks: %d' % nchunks)
    print_verbose('chunk_size: %s' % pretty_size(chunk_size))
    print_verbose('last_chunk_size: %s' % pretty_size(last_chunk_size))
    # calculate header
    bloscpack_header = create_bloscpack_header(nchunks)
    print_verbose('bloscpack_header: %s' % repr(bloscpack_header))
    # write the chunks to the file
    with open(in_file, 'rb') as input_fp, \
         open(out_file, 'wb') as output_fp:
        output_fp.write(bloscpack_header)
        for i, bytes_to_read in enumerate((
                [chunk_size] * (nchunks - 1)) + [last_chunk_size]):
            print_verbose("compressing chunk '%d'%s" %
                    (i, ' (last)' if i == nchunks-1 else ''))
            current_chunk = input_fp.read(bytes_to_read)
            compressed = blosc.compress(current_chunk, **blosc_args)
            output_fp.write(compressed)
            print_verbose("chunk written, in: %s out: %s" %
                    (pretty_size(len(current_chunk)),
                        pretty_size(len(compressed))))
    out_file_size = path.getsize(out_file)
    print_verbose('Output file size: %s' % pretty_size(out_file_size))

def unpack_file(in_file, out_file):
    in_file_size = path.getsize(in_file)
    print_verbose('Input file size: %s' % pretty_size(in_file_size))
    with open(in_file, 'rb') as input_fp, \
         open(out_file, 'wb') as output_fp:
        # read the bloscpack header
        print_verbose('reading bloscpack header')
        bloscpack_header = input_fp.read(8)
        nchunks = decode_bloscpack_header(bloscpack_header, None)
        print_verbose('nchunks: %d' % nchunks)
        for i in range(nchunks):
            print_verbose("decompressing chunk '%d'%s" %
                    (i, ' (last)' if i == nchunks-1 else ''))
            print_verbose('reading blosc header')
            blosc_header_raw = input_fp.read(16)
            blosc_header = read_blosc_header(blosc_header_raw)
            ctbytes = blosc_header['ctbytes']
            print_verbose('ctbytes: %s' % pretty_size(ctbytes))
            # seek back 16 bytes in file relative to current position
            input_fp.seek(-16, 1)
            compressed = input_fp.read(ctbytes)
            decompressed = blosc.decompress(compressed)
            output_fp.write(decompressed)
            print_verbose("chunk written, in: %s out: %s" %
                    (pretty_size(len(compressed)),
                        pretty_size(len(decompressed))))
    out_file_size = path.getsize(out_file)
    print_verbose('Output file size: %s' % pretty_size(out_file_size))

if __name__ == '__main__':
    parser = create_parser()
    args = parser.parse_args()
    VERBOSE = args.verbose
    PREFIX = parser.prog
    print_verbose('command line argument parsing complete')
    print_verbose('command line arguments are: ')
    for arg, val in vars(args).iteritems():
        print_verbose('\t%s: %s' % (arg, str(val)))

    # compression and decompression handled via subparsers
    if args.subcommand == 'compress':
        print_verbose('getting ready for compression')
        in_file, out_file, blosc_args = process_compression_args(args,
                parser.error)
        print_verbose('blosc args are:')
        for arg, value in blosc_args.iteritems():
            print_verbose('\t%s: %s' % (arg, value))
        check_files(in_file, out_file, args, parser.error)
        process_nthread_arg(args)
        pack_file(in_file, out_file, blosc_args)
    elif args.subcommand == 'decompress':
        print_verbose('getting ready for decompression')
        in_file, out_file = process_decompression_args(args, parser.error)
        check_files(in_file, out_file, args, parser.error)
        process_nthread_arg(args)
        unpack_file(in_file, out_file)
    else:
        # we should never reach this
        parser.error('You found the easter-egg, please contact the author')
    print_verbose('done')