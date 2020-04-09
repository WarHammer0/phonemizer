# Copyright 2015-2020 Mathieu Bernard
#
# This file is part of phonemizer: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Phonemizer is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with phonemizer. If not, see <http://www.gnu.org/licenses/>.
"""Espeak backend for the phonemizer"""

import distutils.spawn
import os
import re
import shlex
import subprocess
import tempfile
from functools import lru_cache

from phonemizer.backend.base import BaseBackend
from phonemizer.logger import get_logger
from phonemizer.punctuation import Punctuation
from phonemizer.utils import get_package_resource


# a regular expression to find language switching flags in espeak output,
# Switches have the following form (here a switch from English to French):
# "something (fr) quelque chose (en) another thing".
_ESPEAK_FLAGS_RE = re.compile(r'\(.+?\)')


# a global variable being used to overload the default espeak installed on the
# system. The user can choose an alternative espeak with the method
# EspeakBackend.set_espeak_path().
_ESPEAK_DEFAULT_PATH = None


class EspeakBackend(BaseBackend):
    """Espeak backend for the phonemizer"""

    espeak_version_re = r'.*: ([0-9]+(\.[0-9]+)+(\-dev)?)'

    def __init__(self, language,
                 punctuation_marks=Punctuation.default_marks(),
                 preserve_punctuation=False,
                 use_sampa=False,
                 language_switch='keep-flags', with_stress=False,
                 logger=get_logger()):
        super(self.__class__, self).__init__(
            language, punctuation_marks=punctuation_marks,
            preserve_punctuation=preserve_punctuation, logger=logger)
        self.logger.debug(f'espeak is {self.espeak_path()}')

        # adapt some command line option to the espeak version (for
        # phoneme separation and IPA output)
        version = self.version()

        self.use_sampa = use_sampa
        self.sampa_mapping = self._load_sampa_mapping()

        self.sep = '--sep=_'
        if version == '1.48.03' or version.split('.')[1] <= '47':
            self.sep = ''  # pragma: nocover

        self.ipa = '--ipa=3'
        if self.is_espeak_ng():  # this is espeak-ng
            self.ipa = '-x --ipa'

        self._with_stress = with_stress
        if use_sampa is True:
            self.ipa = '-x --pho'

        # ensure the lang_switch argument is valid
        valid_lang_switch = [
            'keep-flags', 'remove-flags', 'remove-utterance']
        if language_switch not in valid_lang_switch:
            raise RuntimeError(
                'lang_switch argument "{}" invalid, must be in {}'
                .format(language_switch, ", ".join(valid_lang_switch)))
        self._lang_switch = language_switch
        self._lang_switch_list = []

    @staticmethod
    def name():
        return 'espeak'
    
    @staticmethod
    def set_espeak_path(fpath):
        """"""
        global _ESPEAK_DEFAULT_PATH
        if not fpath:
            _ESPEAK_DEFAULT_PATH = None
            return

        if not (os.path.isfile(fpath) and os.access(fpath, os.X_OK)):
            raise ValueError(
                f'{fpath} is not an executable file')

        _ESPEAK_DEFAULT_PATH = os.path.abspath(fpath)

    @staticmethod
    @lru_cache(maxsize=None)
    def espeak_path():
        if _ESPEAK_DEFAULT_PATH:
            return _ESPEAK_DEFAULT_PATH

        if 'PHONEMIZER_ESPEAK_PATH' in os.environ:
            espeak = os.environ['PHONEMIZER_ESPEAK_PATH']
            if not (os.path.isfile(espeak) and os.access(espeak, os.X_OK)):
                raise ValueError(
                    f'PHONEMIZER_ESPEAK_PATH={espeak} '
                    f'is not an executable file')
            return os.path.abspath(espeak)

        espeak = distutils.spawn.find_executable('espeak-ng')
        if not espeak:  # pragma: nocover
            espeak = distutils.spawn.find_executable('espeak')
        return espeak

    @classmethod
    @lru_cache(maxsize=None)
    def is_available(cls):
        return True if cls.espeak_path() else False
    
    @classmethod
    @lru_cache(maxsize=None)
    def long_version(cls):
        return subprocess.check_output(shlex.split(
            '{} --help'.format(cls.espeak_path()), posix=False)).decode(
                'utf8').split('\n')[1]

    @classmethod
    @lru_cache(maxsize=None)
    def is_espeak_ng(cls):
        """Returns True if using espeak-ng, False otherwise"""
        return 'eSpeak NG' in cls.long_version()

    @classmethod
    @lru_cache(maxsize=None)
    def version(cls, as_tuple=False):
        # the full version version string includes extra information
        # we don't need
        long_version = cls.long_version()

        # extract the version number with a regular expression
        try:
            version = re.match(cls.espeak_version_re, long_version).group(1)
        except AttributeError:
            raise RuntimeError(f'cannot extract espeak version from {cls.espeak_path()}')

        if as_tuple:
            version = tuple(int(v) for v in version.split('.'))
        return version

    @classmethod
    @lru_cache(maxsize=None)
    def supported_languages(cls):
        # retrieve the languages from a call to 'espeak --voices'
        voices = subprocess.check_output(shlex.split(
            '{} --voices'.format(cls.espeak_path()), posix=False)).decode(
                'utf8').split('\n')[1:-1]
        voices = [v.split() for v in voices]

        # u'å' cause a bug in python2
        return {v[1]: v[3].replace(u'_', u' ').replace(u'å', u'a')
                for v in voices}

    def _load_sampa_mapping(self):
        """Loads a sampa symbol map from a file in phonemizer/share/espeak

        Returns it as a dictionary. Returns None if such a file does not exist.

        """
        if not self.use_sampa:
            return None

        # look for a file with SAMPA conversion mapping
        filename = os.path.join(
            get_package_resource('espeak'),
            'sampa_{}.txt'.format(self.language))

        if not os.path.isfile(filename):
            return None

        # build the mapping from the file
        self.logger.debug('loading SAMPA mapping from %s', filename)
        mapping = {}
        for line in open(filename, 'r'):
            symbols = line.strip().split()
            if len(symbols) != 2:  # pragma: nocover
                raise ValueError(
                    'bad format in sampa mapping file {}: {}'
                    .format(filename, line))
            mapping[symbols[0]] = symbols[1]
        return mapping

    def _process_lang_switch(self, n, utt):
        # look for language swith in the current utterance
        flags = re.findall(_ESPEAK_FLAGS_RE, utt)

        # no language switch, nothing to do
        if not flags:
            return utt

        # language switch detected, register the line number
        self._lang_switch_list.append(n)

        # ignore the language switch but warn if one is found
        if self._lang_switch == 'keep-flags':
            return utt

        elif self._lang_switch == 'remove-flags':
            # remove all the (lang) flags in the current utterance
            for flag in set(flags):
                utt = utt.replace(flag, '')

        else:  # self._lang_switch == 'remove-utterances':
            # drop the entire utterance
            return None

        return utt

    def _phonemize_aux(self, text, separator, strip):
        output = []
        text_word_to_phoneme_word_mapping = [];
        for n, line in enumerate(text.split('\n'), start=1):
            with tempfile.NamedTemporaryFile('w+', delete=False) as data:
                try:
                    # save the text as a tempfile
                    try:  # python2
                        data.write(line.encode('utf8'))
                    except TypeError:  # python3
                        data.write(line)
                    data.close()

                    # generate the espeak command to run
                    command = '{} -v{} {} -q -X -f {} {}'.format(
                        self.espeak_path(), self.language, self.ipa,
                        data.name, self.sep)

                    if self.logger:
                        self.logger.debug('running %s', command)

                    line = subprocess.check_output(
                        shlex.split(command, posix=False)).decode('utf8')
                finally:
                    os.remove(data.name)

                # espeak can split an utterance into several lines because
                # of punctuation, here we merge the lines into a single one
                line = line.strip().replace('\n', ' ').replace('  ', ' ')

                # due to a bug in espeak-ng, some additional separators can be
                # added at the end of a word. Here a quick fix to solve that
                # issue. See https://github.com/espeak-ng/espeak-ng/issues/694
                line = re.sub(r'_+', '_', line)
                line = re.sub(r'_ ', ' ', line)

                line = self._process_lang_switch(n, line)
                if not line:
                    continue

                # Newly added -X flag will make espeak-ng output word to phoneme word mappings, the output
                # will be as follows: 'this~|||~DIs~|~|~ is~|||~Iz~|~|~ to be~|||~t@bi~|~|~ ðɪs ɪz ɐ təbi tɛst .'
                # Word(s) mappings are delimited by ~|~|~. Original text to phonemized texts are delimited by ~|||~
                mappings_and_phonemized_text = line.split('~|~|~')

                # Set the line variable to the phonemized text only, which will always be the last index in the
                # split string. Mappings always come before phonemized text.
                line = mappings_and_phonemized_text[-1]

                # Mappings are every index except the last index (phonemized text)
                # Not every phonemized sentence will have word mappings, in that case we set mappings to None.
                # Only phonemized sentences with whole word phonemizations. For example, "to be" ~> "t@bi" will contain a mapping,
                # while "test" ~> "t", "ɛ", "s", "t" will not contain a mapping as it was a character-by-character phonemization.
                mappings = mappings_and_phonemized_text[0:-1] if len(mappings_and_phonemized_text) > 1 else None


                # If mappings are not none, loop over each mapping and extract the text to phoneme mappings
                # This will produce a temp_text_word_to_phoneme_word_mapping array which will look like this:
                # [['this', 'DIs'], [' is', 'Iz'], [' to be', 't@bi']]
                temp_text_word_to_phoneme_word_mapping = []
                if mappings is not None:
                    for mapping in mappings:
                        text_and_phoneme = mapping.split('~|||~')

                        # Sometimes text words map to multiple phoneme words. Like "lunchroom" ~> "l'VntS ru:m"
                        # This comes out in the following shape; lunchroom~|||~lVntS||ru:m~|~|~l'VntS ru:m
                        # Without handling it, text_and_phoneme would be equal to the following:
                        # ["lunchroom", "lVntS||ru:m"]
                        # This should be fixed to be in the form of [["lunchroom", "lVntS ru:m"]]
                        #
                        # There is also another case where multiple words will map to multiple phonemes
                        # such as, "a while" ~> "ɐ wˈaɪl". This comes out in the following shape; a while~|||~a#||waIl~|~|~ɐ wˈaɪl
                        # without handling it, text and phonemes would be equal to the following:
                        # ["a while", "a#||waIl"]. THis should be fixed to be in the form of ["a while", "a# waIl"]
                        text_and_phoneme[1] = text_and_phoneme[1].replace("||", " ")
                        # Remove prepended and trailing spaces if they are there.
                        if text_and_phoneme[0][0] == ' ':
                            text_and_phoneme[0] = text_and_phoneme[0][1:]
                        if text_and_phoneme[0][-1] == ' ':
                            text_and_phoneme[0] = text_and_phoneme[0][0:-1]

                        temp_text_word_to_phoneme_word_mapping.append(text_and_phoneme)

                # Append the mappings to the array:
                text_word_to_phoneme_word_mapping.append(temp_text_word_to_phoneme_word_mapping)

                out_line = ''
                for word in line.split(u' '):
                    w = word.strip()

                    # remove the stresses on phonemes
                    if not self._with_stress:
                        w = w.replace(u"ˈ", u'')
                        w = w.replace(u'ˌ', u'')
                        w = w.replace(u"'", u'')
                        w = w.replace(u"-", u'')

                    # replace the SAMPA symbols from espeak output to the
                    # standardized ones
                    if self.sampa_mapping:
                        for k, v in self.sampa_mapping.items():
                            w = w.replace(k, v)

                    if not strip:
                        w += '_'
                    w = w.replace('_', separator.phone)
                    out_line += w + separator.word

                if strip:
                    out_line = out_line[:-len(separator.word)]
                output.append(out_line)

        # warn the user on language switches found during phonemization
        if self._lang_switch_list:
            nswitches = len(self._lang_switch_list)
            if self._lang_switch == 'remove-utterance':
                self.logger.warning(
                    'removed %s utterances containing language switches '
                    '(applying "remove-utterance" policy)', nswitches)
            else:
                self.logger.warning(
                    'found %s utterances containing language switches '
                    'on lines %s', nswitches,
                    ', '.join(str(l) for l in self._lang_switch_list))
                self.logger.warning(
                    'extra phones may appear in the "%s" phoneset',
                    self.language)
                if self._lang_switch == "remove-flags":
                    self.logger.warning(
                        'language switch flags have been removed '
                        '(applying "remove-flags" policy)')
                else:
                    self.logger.warning(
                        'language switch flags have been kept '
                        '(applying "keep-flags" policy)')

        return output, text_word_to_phoneme_word_mapping
