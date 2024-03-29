#!/usr/bin/env python
#This file is released under terms of BSD license`
#See LICENSE.txt for more information


from __future__ import print_function

"""
pp_ser.py

Parser to expand $!SER serialization directives in Fortran code in order to generate
serialization code using the m_serialize.f90 interface for the STELLA serialization
framework.

The grammar is defined by a set of !$SER directives. All directives are case-
insensitive. The main keywords are INIT for initialization, VERBATIM for echoeing
some Fortran statements, OPTION for setting specific options for the serialization
modlue, REGISTER for registering a data field meta-information,
ZERO for setting some field to zero, SAVEPOINT for registering a savepoint with some
optional information, DATA for serializing a data field, and CLEANUP for finishing
serialization.

The implementation uses two passes. The first pass collects all necessary calls
which is then used for a corresponding USE statement importing the necessary
methods from the Fortran serialization module. The second pass expands the directives.
"""

# information
__author__    = 'Oliver Fuhrer'
__copyright__ = 'Copyright 2014, MeteoSwiss'
__license__   = 'GPL'
__version__   = '0.1'
__date__      = 'Sun Mar 23 22:06:44 2014'
__email__     = 'oliver.fuhrer@meteoswiss.ch'

# modules
import sys, os, tempfile, re, filecmp, shutil

def toASCII(text):
    if sys.version_info[0] == 3:
        return bytes(text, 'ascii')
    else:
        return str(text)

class pp_ser:

    def __init__(self, infile, outfile='', ifdef='SERIALIZE', real='ireals',
                 module='m_serialize', identical=True, verbose=False):

        # public variables
        self.verbose = verbose
        self.infile = infile        # input file
        self.outfile = outfile      # output file
        self.ifdef = ifdef          # write #ifdef/#endif blocks
        self.real = real            # name of real type (Fortran)
        self.module = module        # name of Fortran module which contains serialization methods
        self.identical = identical  # write identical files (no preprocessing done)?

        # setup (also public)
        self.methods = {
            'mode'            : 'ppser_set_mode',
            'getmode'         : 'ppser_get_mode',
            'init'            : 'ppser_initialize',
            'cleanup'         : 'ppser_finalize',
            'data'            : 'fs_write_field',
            'datawrite'       : 'fs_write_field',
            'dataread'        : 'fs_read_field',
            'option'          : 'fs_Option',
            'serinfo'         : 'fs_add_serializer_metainfo',
            'register'        : 'fs_register_field',
            'registertracers' : 'fs_RegisterAllTracers',
            'fieldmetainfo'   : 'fs_AddFieldMetaInfo',
            'savepoint'       : 'fs_create_savepoint',
            'spinfo'          : 'fs_add_savepoint_metainfo',
            'fieldinfo'       : 'fs_add_field_metainfo',
            'on'              : 'fs_enable_serialization',
            'off'             : 'fs_disable_serialization'
        }

        # language definition (also public)
        self.language = {
            'cleanup'           : ['CLEANUP', 'CLE'],
            'data'              : ['DATA', 'DAT'],
            'mode'              : ['MODE', 'MOD'],
            'init'              : ['INIT', 'INI'],
            'option'            : ['OPTION', 'OPT'],
            'metainfo'          : ['METAINFO'],
            'verbatim'          : ['VERBATIM', 'VER'],
            'register'          : ['REGISTER', 'REG'],
            'registertracers'   : ['REGISTERTRACERS'],
            'zero'              : ['ZERO', 'ZER'],
            'savepoint'         : ['SAVEPOINT', 'SAV'],
            'tracer'            : ['TRACER', 'TRA'],
            'registertracers'   : ['REGISTERTRACERS'],
            'cleanup'           : ['CLEANUP', 'CLE'],
            'on'                : ['ON'],
            'off'               : ['OFF']
        }

        self.modes = {
            'write'   : 0,
            'read'    : 1,
            'CPU'     : 0,
            'GPU'     : 1
        }

        self.intentin_to_remove = []
        self.intentin_removed = []

        # private variables
        self.__ser = False       # currently processing !$SER directives
        self.__line = ''         # current line
        self.__linenum = 0       # current line number
        self.__module = ''       # current module
        self.__calls = set()     # calls to serialization module
        self.__outputBuffer = '' # preprocessed file
        self.__implicitnone_found = False # has a implicit none statement been found?
        self.__implicitnone_done = False # has code been inserted at IMPLICIT NONE?

    # shortcuts for field registering
    def __reg_shortcuts(self, shortcut):
        shortcut = shortcut.upper()
        l = []
        if re.match('(^$|[IJK][IJK1-9]*)',shortcut):
            if shortcut == '':
                l = '1 1 1 1 0 0 0 0 0 0 0 0'.split()
            elif shortcut == 'I':
                l = 'ie 1 1 1 nboundlines nboundlines 0 0 0 0 0 0'.split()
            elif shortcut == 'J':
                l = '1 je 1 1 0 0 nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'J2':
                l = '1 je 2 1 0 0 nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'K':
                l = '1 1 ke 1 0 0 0 0 0 0 0 0'.split()
            elif shortcut == 'K1':
                l = '1 1 ke1 1 0 0 0 0 0 1 0 0'.split()
            elif shortcut == 'IJ':
                l = 'ie je 1 1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'IJ3':
                l = 'ie je 3 1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'IK':
                l = 'ie 1 ke 1 nboundlines nboundlines 0 0 0 0 0 0'.split()
            elif shortcut == 'IK1':
                l = 'ie 1 ke1 1 nboundlines nboundlines 0 0 0 1 0 0'.split()
            elif shortcut == 'JK':
                l = '1 je ke 1 0 0 nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'JK1':
                l = '1 je ke1 1 0 0 nboundlines nboundlines 0 1 0 0'.split()
            elif shortcut == 'IJK':
                l = 'ie je ke 1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0'.split()
            elif shortcut == 'IJK1':
                l = 'ie je ke1 1 nboundlines nboundlines nboundlines nboundlines 0 1 0 0'.split()
        return l

    # error handling
    def __exit_error(self, directive = '', msg = ''):
        print('File: "' + self.infile + '", line ' + str(self.__linenum))
        if directive:
            print('SyntaxError: Invalid !$SER ' + directive + ' directive')
        if msg:
            print('Message: '+msg)
        if self.__line:
            print('Line '+str(self.__linenum)+': '+self.__line)
        sys.exit(1)

    # general SER directive arguments parser
    def __ser_arg_parse(self, args):
        # returns list of directives, lists of key=value pairs and (optional) IF statmenet
        dirs = []    # directives
        keys = []    # keys
        values = []  # values
        if_encountered = False
        if_statement = ''
        for arg in args[1:]:
            if arg.upper() == 'IF':
                if_encountered = True
                continue
            if if_encountered:
                if if_statement:
                    self.__exit_error(directive = args[0],
                                      msg = 'IF statement must be last argument')
                if_statement = arg
            else:
                val = arg.split('=')
                if len(val) == 1:
                    dirs.append(val[0])
                elif len(val) == 2:
                    keys.append(val[0])
                    values.append(val[1])
                else:
                    self.__exit_error(directive = args[0],
                                      msg = 'Problem extracting arguments and key=value pairs')
        return dirs, keys, values, if_statement

    # parser for tracer directive
    def __ser_tracer_parse(self, args):
        tracersspec = []
        if_encountered = False
        if_statement = ''

        pattern  = '^([a-zA-Z_0-9]+|\$[a-zA-Z_0-9\(\)]+(?:-[a-zA-Z_0-9\(\)]+)?|%all)' # Tracer name, id(s) or %all
        pattern += '(?:#(tens|bd|surf|sedimvel))?' # Type (stype)
        pattern += '(?:@([a-zA-Z_0-9]+))?' # Time level (timelevel)
        r = re.compile(pattern)

        for arg in args[1:]:
            if arg.upper() == 'IF':
                if_encountered = True
                continue
            if if_encountered:
                if if_statement:
                    self.__exit_error(directive = args[0],
                                      msg = 'IF statement must be last argument')
                if_statement = arg
            else:
                m = r.search(arg)
                if m is None:
                    self.__exit_error(directive = args[0],
                                      msg = 'Tracer specification ' + arg + ' is invalid')
                tracersspec.append(m.groups())
        return tracersspec, if_statement

    # INIT directive
    def __ser_init(self, args):

        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)

        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '

        l += tab + 'PRINT *, \'>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<\'\n'
        l += tab + 'PRINT *, \'>>> WARNING: SERIALIZATION IS ON <<<\'\n'
        l += tab + 'PRINT *, \'>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<\'\n'
        l += tab + '\n'
        l += tab + '! setup serialization environment\n'

        args_lower = [item.lower() for item in args]
        if ('if' in args_lower):
            if_pos = args_lower.index('if'.lower())
        else:
            if_pos = len(args)

        l += tab + 'call ' + self.methods['init'] + '(' + ','.join(args[1:if_pos]) + ')\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__calls.add(self.methods['init'])
        self.__line = l

    # OPTION directive
    def __ser_option(self, args):
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        if len(dirs) != 0:
            self.__exit_error(directive = args[0],
                              msg = 'Must specify a name and a list of key=value pairs')
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '
        l += 'call ' + self.methods['option'] + '('
        for i in range(len(keys)):
            if keys[i].lower() == 'verbosity':
                if values[i].lower() == 'off':
                    values[i]='0'
                if values[i].lower() == 'on':
                    values[i]='1'
            if i==0:
                l += keys[i] + '=' + values[i]
            else:
                l += ', ' + keys[i] + '=' + values[i]
        l += ')\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__calls.add(self.methods['option'])
        self.__line = l

    # METAINFO directive
    def __ser_metainfo(self, args):
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        l, tab = '', ''
        self.__calls.add(self.methods['serinfo'])
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '
        for k,v in zip(keys, values):
            l += tab + 'CALL ' + self.methods['serinfo'] + '(ppser_serializer, "' + k + '", ' + v + ')\n'
        for d in dirs:
            l += tab + 'CALL ' + self.methods['serinfo'] + '(ppser_serializer, "' + d + '", ' + d + ')\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__line = l

    # VERBATIM directive
    def __ser_verbatim(self, args):
        # simply remove $!SER directive for verbatim statements
        self.__line = ' '.join(args[1:]) + '\n'

    # REGISTER directive
    def __ser_register(self, args):

        # parse arguments
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        if len(dirs) < 2:
            self.__exit_error(directive = args[0],
                              msg = 'Must specify a name, a type and the field sizes')

        if len(dirs) == 2:
            dirs.append('')

        # quote name
        dirs[0] = "'" + dirs[0] + "'"

        # data type
        datatypes = dict(integer=["'int'", 'ppser_intlength'], real=['ppser_realtype', 'ppser_reallength'])
        dirs[1:2] = datatypes[dirs[1]]
        #try:
        #    dirs[1:2] = datatypes[dirs[1]]
        #except KeyError:
        #    self.__exit_error(directive = args[0],
        #                      msg = 'Data type '+dirs[1]+' is not recognized. Valid type are '
        #                            + '"integer" and "real"')

        # implement some shortcuts for often recurring patterns
        if len(dirs) == 4:
            l = self.__reg_shortcuts(dirs[3])
            if l:
                dirs[3:4] = l

        # REGISTER [arg ...]
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '

        # registration
        self.__calls.add(self.methods['register'])
        l += tab + 'call ' + self.methods['register'] + '(ppser_serializer, ' + ', '.join(dirs) + ')\n'

        # metainfo
        if len(keys) > 0:
            self.__exit_error(directive = args[0],
                              msg = 'Metainformation for fields are not yet implemented')
        #for k,v in zip(keys, values):
        #    l += tab + 'call ' + self.methods['fieldmetainfo'] + '(ppser_serializer, ' + dirs[0] + ', "' + k + '", ' + v + ')\n'

        if if_statement:
            l += 'ENDIF\n'

        self.__line = l

    # REGISTERTRACERS directive
    def __ser_registertracers(self, args):
        l = 'call fs_RegisterAllTracers()\n'
        self.__calls.add(self.methods['registertracers'])
        self.__line = l

    # ZERO directive
    def __ser_zero(self, args):
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        if len(keys) > 0:
            self.__exit_error(directive = args[0],
                              msg = 'Must specify a list of fields')
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '
        for arg in dirs:
            l += tab + arg + ' = 0.0_' + self.real + '\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__line = l

    # SAVEPOINT directive
    def __ser_savepoint(self, args):
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        # extract save point name
        if len(dirs) != 1:
            self.__exit_error(directive = args[0],
                              msg = 'Must specify a name and a list of key=value pairs')
        name = dirs[0]
        # generate serialization code
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '
        self.__calls.add(self.methods['savepoint'])
        self.__calls.add(self.methods['spinfo'])
        l += tab + 'call ' + self.methods['savepoint'] + '(\'' + name + '\', ppser_savepoint)\n'
        for k,v in zip(keys, values):
            l += tab + 'call ' + self.methods['spinfo'] + '(ppser_savepoint, \'' + k + '\', ' + v + ')\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__line = l

    # MODE directive
    def __ser_mode(self, args):
        self.__calls.add(self.methods['mode'])
        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '
        if args[1] in self.modes:
            l += tab + 'call ' + self.methods['mode'] + '(' + str(self.modes[args[1]]) + ')\n'
        else:
            l += tab + 'call ' + self.methods['mode'] + '(' + args[1] + ')\n'
        if if_statement:
            l += 'ENDIF\n'
        self.__line = l
            

    # DATA directive
    def __ser_data(self, args):

        (dirs, keys, values, if_statement) = self.__ser_arg_parse(args)

        # only key=value pairs with optional removeintentin allowed
        if len(dirs) != 0:
            if not(len(dirs) == 1 and 'removeintentin' in dirs):
                self.__exit_error(directive = args[0],
                                  msg = 'Must specify a list of key=value pairs with optional removeintentin')
        
        # generate serialization code
        self.__calls.add(self.methods['datawrite'])
        self.__calls.add(self.methods['dataread'])
        self.__calls.add(self.methods['getmode'])
        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '

        if 'removeintentin' in dirs:
            for v in values:
                v = re.sub(r'\(.+\)', '', v)
                if v not in self.intentin_to_remove:
                    self.intentin_to_remove.append(v)
     
        l += tab + 'SELECT CASE ( ' + self.methods['getmode'] + '() )\n'
        l += tab + '  ' + 'CASE(' + str(self.modes['write']) + ')\n'
        for k,v in zip(keys, values):
            l += tab + '    ' + 'ACC_PREFIX UPDATE HOST ( ' + v + ' ), IF (i_am_accel_node) \n'
            l += tab + '    ' + 'call ' + self.methods['datawrite'] + '(ppser_serializer, ppser_savepoint, \'' + k + '\', ' + v + ')\n'
        l += tab + '  ' + 'CASE(' + str(self.modes['read']) + ')\n'
        for k,v in zip(keys, values):
            l += tab + '    ' + 'call ' + self.methods['dataread'] + '(ppser_serializer_ref, ppser_savepoint, \'' + k + '\', ' + v + ')\n'
            l += tab + '    ' + 'ACC_PREFIX UPDATE DEVICE ( ' + v + ' ), IF (i_am_accel_node) \n'
        l += tab + 'END SELECT\n'
        
        if if_statement:
            l += 'ENDIF\n'
        self.__line = l

   # TRACER directive
    def __ser_tracer(self, args):

        (tracerspec, if_statement) = self.__ser_tracer_parse(args)

        l = ''
        tab = ''
        if if_statement:
            l += 'IF (' + if_statement + ') THEN\n'
            tab = '  '

        for t in tracerspec:
            function = 'ppser_write_tracer_'
            fargs = []

            # Function name and first arguments
            if t[0] == '%all':
                # %all specifier
                function += 'all'
            elif t[0][0] == '$':
                # Index-based access
                function += 'bx_idx'
                idxs = t[0][1:]
                if '-' in idxs:
                    fargs += idxs.split('-')
                else:
                    fargs += [idxs]
            else:
                # Name-based access
                function += 'by_name'
                fargs.append("'" + t[0] + "'")

            # Required stype argument
            fargs.append("stype='" + (t[1] or '') + "'")

            # Other arguments
            for i, argname in enumerate(('timelevel',), 2):
                if t[i]:
                    fargs.append(argname + '=' + t[i])

            # Put together function call
            self.__calls.add(function)
            l += tab + 'call ' + function + '(' + ', '.join(fargs) + ')\n'

        if if_statement:
            l += 'ENDIF\n'
        self.__line = l


    # CLEANUP directive
    def __ser_cleanup(self, args):
        l = ''
        l += '! cleanup serialization environment\n'
        l += 'call ' + self.methods['cleanup'] + '(' + ','.join(args[1:]) + ')\n'
        self.__calls.add(self.methods['cleanup'])
        self.__line = l

    # ON directive
    def __ser_on(self, args):
        l = 'call ' + self.methods['on'] + '()\n'
        self.__calls.add(self.methods['on'])
        self.__line = l

    # OFF directive
    def __ser_off(self, args):
        l = 'call ' + self.methods['off'] + '()\n'
        self.__calls.add(self.methods['off'])
        self.__line = l

    # LINE: module/program
    def __re_module(self):
        r = re.compile('^ *(module|program) +([a-z][a-z0-9_]*)', re.IGNORECASE)
        m = r.search(self.__line)
        if m:
            if m.group(2).upper() == 'PROCEDURE':
                return False
            if self.__module:
                self.__exit_error(msg = 'Unexpected ' + m.group(1) + ' statement')
            self.__module = m.group(2)
        return m

    # LINE: implicit none
    def __re_implicit(self):
        r = re.compile('^ *implicit +none', re.IGNORECASE)
        m = r.search(self.__line)
        if m:
            calls_pp = [c for c in self.__calls if     c.startswith('ppser')]
            calls_fs = [c for c in self.__calls if not c.startswith('ppser')]
            ncalls = len(calls_pp) + len(calls_fs)
            if ncalls > 0:
                calls_pp += ['ppser_savepoint', 'ppser_serializer', 'ppser_serializer_ref',
                             'ppser_intlength', 'ppser_reallength', 'ppser_realtype']
            if ncalls > 0 and not self.__implicitnone_done:
                l = '\n'
                if self.ifdef:
                    l += '#ifdef ' + self.ifdef + '\n'
                if len(calls_fs) > 0:
                    l += 'USE ' + self.module + ', ONLY: ' + ', '.join(calls_fs) + '\n'
                if len(calls_pp) > 0:
                    # calls_str = ', '.join(calls_pp)
                    # calls_str = ' &\n                       '.join(textwrap.wrap(calls_str, 80))
                    # l         +=    'USE utils_ppser, ONLY: ' + calls_str + '\n'
                    l += 'USE utils_ppser, ONLY: ' + ', '.join(calls_pp) + '\n'
                if self.ifdef:
                    l += '#endif\n'
                l += '\n'
                self.__line = l + self.__line
                self.__implicitnone_done = True
            self.__implicitnone_found = True
        return m

    # LINE: !$SER directive
    def __re_ser(self):
        r1 = re.compile('^ *!\$ser *(.*)$', re.IGNORECASE)
        r2 = re.compile(r'''((?:[^ "']|"[^"]*"|'[^']*')+)''', re.IGNORECASE)
        m = r1.search(self.__line)
        if m:
            if m.group(1):
                args = r2.split(m.group(1))[1::2]
                if   args[0].upper() in self.language['init']:
                    self.__ser_init(args)
                elif args[0].upper() in self.language['option']:
                    self.__ser_option(args)
                elif args[0].upper() in self.language['metainfo']:
                    self.__ser_metainfo(args)
                elif args[0].upper() in self.language['verbatim']:
                    self.__ser_verbatim(args)
                elif args[0].upper() in self.language['register']:
                    self.__ser_register(args)
                elif args[0].upper() in self.language['savepoint']:
                    self.__ser_savepoint(args)
                elif args[0].upper() in self.language['zero']:
                    self.__ser_zero(args)
                elif args[0].upper() in self.language['data']:
                    self.__ser_data(args)
                elif args[0].upper() in self.language['tracer']:
                    self.__ser_tracer(args)
                elif args[0].upper() in self.language['registertracers']:
                    self.__ser_registertracers(args)
                elif args[0].upper() in self.language['cleanup']:
                    self.__ser_cleanup(args)
                elif args[0].upper() in self.language['on']:
                    self.__ser_on(args)
                elif args[0].upper() in self.language['off']:
                    self.__ser_off(args)
                elif args[0].upper() in self.language['mode']:
                    self.__ser_mode(args)
                else:
                    self.__exit_error(directive = args[0],
                                      msg = 'Unknown directive encountered')
        return m

    # LINE: end module/end program
    def __re_endmodule(self):
        r = re.compile('^ *end *(module|program) ([a-z][a-z0-9_]*)', re.IGNORECASE)
        m = r.search(self.__line)
        if m:
            if not self.__module:
                self.__exit_error(msg = 'Unexpected "end '+m.group(1)+'" statement')
            if self.__module != m.group(2):
                self.__exit_error(msg = 'Was expecting "end '+m.group(1)+' '+self.__module+'"')
            self.__module = ''
        return m

    def __re_def(self):
        r = re.compile(r'.*intent *\(in\)[^:]*::\s+(.*)', re.IGNORECASE)
        m = r.search(self.__line)
        if m:
            splitted = self.__line.split('::')
            var_with_dim = [x.strip().replace(' ', '') for x in re.split(r',(?![^(]*\))', splitted[1])]
            var = [re.sub(r'\(.*?\)', '', x) for x in var_with_dim]

            fields_in_this_line = [x for x in self.intentin_to_remove if x in var]
            self.intentin_removed.extend([x for x in fields_in_this_line if x not in self.intentin_removed])

            if fields_in_this_line:
                l =  '#ifdef ' + self.ifdef + '\n'                
                l += re.sub(r', *intent *\(in\)', '', self.__line, flags=re.IGNORECASE)
                l += '#else\n' + self.__line + '#endif\n'

                self.__line = l
            return fields_in_this_line
        return m

    # evaluate one line
    def lexer(self, final=False):

        # parse lines related to scope
        self.__re_module()
        self.__re_implicit()
        self.__re_endmodule()
        self.__re_def()

        # parse !$SER lines
        if self.__re_ser():
            # if this is the first line with !$SER statements, add #ifdef
            if self.ifdef and not self.__ser:
                self.__line = '#ifdef ' + self.ifdef + '\n' + self.__line
                self.__ser = True
        else:
            # if this is the first line without !$SER statements, add #endif
            if self.ifdef and self.__ser:
                self.__line = '#endif\n' + self.__line
                self.__ser = False

        if final:
            # final call, check consistency
            if self.__ser:
                self.__exit_error(msg = 'Unterminated #ifdef ' + self.ifdef + ' encountered')
            if self.__module:
                self.__exit_error(msg = 'Unterminated module or program unit encountered')
            if len(self.__calls) > 0 and not self.__implicitnone_found:
                self.__exit_error(msg = 'No IMPLICIT NONE statement found in code')

    # execute one parsing pass over file
    def parse(self, generate=False):
        # if generate == False we only analyse the file

        # reset flags (which define state of parser)
        self.__ser = False       # currently processing !$SER directives
        self.__line = ''         # current line
        self.__linenum = 0       # current line number
        self.__module = ''       # current module
        self.__outputBuffer = '' # preprocessed file
        self.__implicitnone_found = False # has a implicit none statement been found?
        self.__implicitnone_done = False # has code been inserted at IMPLICIT NONE?

        # open and parse file
        input_file = open(os.path.join(self.infile), 'r')
        try:
            self.line = ''
            for line in input_file:
                # handle line continuation (next line coming in)
                if self.__line:
                    if re.match('^ *!\$ser& ', line, re.IGNORECASE):
                        line = re.sub('^ *!\$ser& *', ' ', line, re.IGNORECASE)
                    else:
                        self.__exit_error(msg = 'Incorrect line continuation encountered')
                self.__line += line
                self.__linenum += 1
                # handle line continuation (continued line going out)
                if re.match('^ *!\$ser *(.*) & *$', self.__line, re.IGNORECASE):
                    # chop trailing &
                    self.__line = re.sub(' +& *$', '', self.__line, re.IGNORECASE)
                    self.__line = self.__line.rstrip()
                    continue
                # parse line
                self.lexer()
                if generate:
                    self.__outputBuffer += self.__line
                # cleanup current line (used for line continuation and final lexer call)
                self.__line = ''
            self.lexer(final=True)

            if generate and (len(self.intentin_to_remove) != len(self.intentin_removed)):
                diff = [x for x in self.intentin_to_remove if x not in self.intentin_removed]
                self.__exit_error(msg = 'cannot find INTENT(IN) declaration for ' + ', '.join(diff))

        finally:
            input_file.close()

    # main processing method
    def preprocess(self):
        # parse file
        self.parse()                # first pass, analyse only
        self.parse(generate=True)   # second pass, preprocess
        # write output
        if self.outfile != '':
            output_file = tempfile.NamedTemporaryFile(delete = False)
            output_file.write(toASCII(self.__outputBuffer))
            output_file.close()
            useit = True
            if os.path.isfile(self.outfile) and not self.identical:
                if filecmp.cmp(self.outfile, output_file.name):
                    useit = False
            if useit:
                try:
                    os.rename(output_file.name, self.outfile)
                except:
                    shutil.move(output_file.name, self.outfile)
            else:
                os.remove(output_file.name)
        else:
            print(self.__outputBuffer)

def simple_test():
    try:
        test = """
module test
implicit none

!$SER VERBATIM CHARACTER (LEN=6) :: fs_realtype

!$SER INIT singlefile=.true.
!$SER ZERO a b c d
!$SER SAVEPOINT gugus
!$SER SAVEPOINT DycoreUnittest.DoStep-in LargeTimeStep=ntstep Test=Blabla IF ntstep>0
! this is a comment
!$SER DATA u=u(:,:,:,nnow)
!$SER DATA v=v_in(:,:,:)+v_ref(:,:,:,nnow) IF allocated(v_in)
!$SER DATA test='  gugjs is here ' IF a==' this is a test '
!$SER DATA nsmsteps=REAL(nsmsteps,ireals)
!$SER DATA u=u(:,:,:,nnew) u_nnow=u(:,:,:,nnow) v=v(:,:,:,nnew) v_nnow=v(:,:,:,nnow) IF ntstep>0

!$SER VERBATIM ! REAL field type
!$SER VERBATIM SELECT CASE (ireals)
!$SER VERBATIM   CASE (ireals4) ; fs_realtype = 'float'
!$SER VERBATIM   CASE (ireals8) ; fs_realtype = 'double'
!$SER VERBATIM END SELECT

!$SER REG u fs_realtype IJK
!$SER REGISTER u          fs_realtype ie je ke  1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0
!$SER REG w fs_realtype IJK1
!$SER REGISTER w          fs_realtype ie je ke1 1 nboundlines nboundlines nboundlines nboundlines 0 1 0 0
!$SER REG cpollen2_s fs_realtype IJ
!$SER REGISTER cpollen2_s fs_realtype ie je 1   1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0
!$SER REGISTER dts fs_realtype
!$SER REGISTER nlastbound 'integer' 1
!$SER REG wgtfacq fs_realtype IJ3
!$SER REGISTER wgtfacq fs_realtype ie je 3 1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0
!$SER REG vcoord fs_realtype K1
!$SER REGISTER vcoord fs_realtype KSize=ke1 KPlusHalo=1
!$SER REG crlat fs_realtype J2
!$SER REGISTER crlat fs_realtype JSize=je JMinusHalo=nboundlines JPlusHalo=nboundlines KSize=2
!$SER REG tgrlat J
!$SER REGISTER tgrlat fs_realtype JSize=je JMinusHalo=nboundlines JPlusHalo=nboundlines
!$SER REG a1t fs_realtype K1
!$SER REGISTER a1t fs_realtype KSize=ke1 KPlusHalo=1
!$SER REG lwest_lbdz 'integer' IJ
!$SER REGISTER lwest_lbdz 'integer' ie je 1 1 nboundlines nboundlines nboundlines nboundlines 0 0 0 0
!$SER DATA lwest_lbdz=merge(1,0,lwest_lbdz)

!$SER VERBATIM ! recalculate bottom boundary condition for serialization
!$SER VERBATIM CALL w_bbc_var(zuhl(:,:,ke1), zvhl(:,:,ke1), zphl(:,:,:), zfx, zfyd)
!$SER DATA wbbc=zphl(:,:,ke1)

! check line continuation
!$ser data gugu=dada &
!$ser&     test=igi dede=a+3+f &
!$ser&     check=in

!$SER VERBATIM #ifdef POLLEN
!$SER VERBATIM IF (ALLOCATED(cpollen))
!$SER DATA cpollen1=cpollen(:,:,:,1,nnew) IF isp_pollen>0
!$SER DATA cpollen2=cpollen(:,:,:,2,nnew) IF isp_pollen>1
!$SER VERBATIM ENDIF
!$SER VERBATIM #endif

!$SER CLEANUP
!$SER

end module test
"""
        f = tempfile.NamedTemporaryFile(delete = False)
        f.write(test)
        f.close()
        ser = pp_ser(f.name)
        pp_ser.real = 'wp'
        ser.preprocess()
    finally:
        os.remove(f.name)

def parse_args():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option('-i', '--ignore-identical', help='Ignore files which are not modified by pre-processor',
               default=False, action='store_true', dest='ignore_identical')
    parser.add_option('-d', '--output-dir', help='The target directory for writing pre-processed files',
               default='', type=str, dest='output_dir')
    parser.add_option('-v', '--verbose', help='Enable verbose execution',
               default=False, action='store_true', dest='verbose')
    (options, args) = parser.parse_args()
    if len(args) < 1:
        parser.error('Need at least one source file to process')
    return (options, args)

if __name__ == "__main__":
    (options,args) = parse_args()
    for infile in args:
        if options.output_dir:
            outfile = os.path.join(options.output_dir, os.path.basename(infile))
        else:
            outfile = ''

        # If output is to a file and the file is more updated than the input, skip
        if os.path.exists(outfile) and os.path.getctime(outfile) > os.path.getctime(infile):
            print('Skipping', infile)
        else:
            print('Processing file', infile)
            ser = pp_ser(infile, real='wp', outfile=outfile, identical=(not options.ignore_identical), verbose=options.verbose)
            ser.preprocess()
