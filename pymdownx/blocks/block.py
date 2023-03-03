"""Block class."""
from abc import ABCMeta, abstractmethod
import functools
import copy
import re
from markdown import util as mutil

RE_IDENT = re.compile(
    r'''
    (?:(?:-?(?:[^\x00-\x2f\x30-\x40\x5B-\x5E\x60\x7B-\x9f])+|--)
    (?:[^\x00-\x2c\x2e\x2f\x3A-\x40\x5B-\x5E\x60\x7B-\x9f])*)
    ''',
    re.I | re.X
)

RE_INDENT = re.compile(r'(?m)^([ ]*)[^ \n]')

RE_DEDENT = re.compile(r'(?m)^([ ]*)($)?')


def _type_multi(value, types=None):
    """Multi types."""

    for t in types:
        try:
            return t(value)
        except ValueError:
            pass

    raise ValueError("Type '{}' did not match any of the provided types".format(type(value)))


def type_multi(*args):
    """Validate a type with multiple type functions."""

    return functools.partial(_type_multi, types=args)


def type_any(value):
    """Accepts any type."""

    return value


def type_none(value):
    """Ensure type None or fail."""

    if value is not None:
        raise ValueError('{} is not None'.format(type(value)))


def _ranged_number(value, minimum, maximum, number_type):
    """Check the range of the given number type."""

    value = number_type(value)
    if minimum is not None and value < minimum:
        raise ValueError('{} is not greater than {}'.format(value, minimum))

    if maximum is not None and value > maximum:
        raise ValueError('{} is not greater than {}'.format(value, minimum))

    return value


def type_number(value):
    """Ensure type number or fail."""

    if not isinstance(value, (float, int)):
        raise ValueError("Could not convert type {} to a number".format(type(value)))

    return value


def type_integer(value):
    """Ensure type integer or fail."""

    if not isinstance(value, int):
        if not isinstance(value, float) or not value.is_integer():
            raise ValueError("Could not convert type {} to an integer".format(type(value)))
        value = int(value)

    return value


def type_ranged_number(minimum=None, maximum=None):
    """Ensure typed number is within range."""

    return functools.partial(_ranged_number, minimum=minimum, maximum=maximum, number_type=type_number)


def type_ranged_integer(minimum=None, maximum=None):
    """Ensured type integer is within range."""

    return functools.partial(_ranged_number, minimum=minimum, maximum=maximum, number_type=type_integer)


def type_boolean(value):
    """Ensure type boolean or fail."""

    if not isinstance(value, bool):
        raise ValueError("Could not convert type {} to a boolean".format(type(value)))
    return value


type_ternary = type_multi(type_none, type_boolean)


def type_string(value):
    """Ensure type string or fail."""

    if isinstance(value, str):
        return value

    raise ValueError("Could not convert type {} to a string".format(type(value)))


def type_string_insensitive(value):
    """Ensure type string and normalize case."""

    return type_string(value).lower()


def type_html_identifier(value):
    """Ensure type HTML attribute name or fail."""

    value = type_string(value)
    m = RE_IDENT.fullmatch(value)
    if m is None:
        raise ValueError('A valid attribute name must be provided')
    return m.group(0)


def _delimiter(string, split, string_type):
    """Split the string by the delimiter and then parse with the parser."""

    l = []
    # Ensure input is a string
    string = type_string(string)
    for s in string.split(split):
        s = s.strip()
        if not s:
            continue
        # Ensure each part conforms to the desired string type
        s = string_type(s)
        l.append(s)
    return l


def _string_in(value, accepted, string_type):
    """Ensure type string is within the accepted values."""

    value = string_type(value)
    if value not in accepted:
        raise ValueError('{} not found in {}'.format(value, str(accepted)))
    return value


def type_string_in(accepted, insensitive=True):
    """Ensure type string is within the accepted list."""

    return functools.partial(
        _string_in,
        accepted=accepted,
        string_type=type_string_insensitive if insensitive else type_string
    )


def type_string_delimiter(split, string_type=type_string):
    """String delimiter function."""

    return functools.partial(_delimiter, split=split, string_type=string_type)


def type_html_attribute_dict(value):
    """Attribute dictionary."""

    if not isinstance(value, dict):
        raise ValueError('Attributes should be contained within a dictionary')

    attributes = {}
    for k, v in value.items():
        k = type_html_identifier(k)
        if k.lower() == 'class':
            k = 'class'
            v = type_html_classes(v)
        elif k.lower() == 'id':
            k = 'id'
            v = type_html_identifier(v)
        else:
            v = type_string(v)
        attributes[k] = v

    return attributes


# Ensure class(es) or fail
type_html_classes = type_string_delimiter(' ', type_html_identifier)


class Block(metaclass=ABCMeta):
    """Block."""

    # Set to something if argument should be split.
    # Arguments will be split and white space stripped.
    NAME = ''

    # Instance arguments and options
    ARGUMENT = False
    OPTIONS = {}

    # Extension config
    CONFIG = {}

    def __init__(self, length, tracker, block_mgr, config):
        """
        Initialize.

        - `length` specifies the length (number of slashes) that the header used
        - `tracker` is a persistent storage for the life of the current Markdown page.
          It is a dictionary where we can keep references until the parent extension is reset.
        - `md` is the Markdown object just in case access is needed to something we
          didn't think about.

        """

        # Setup up the argument and options spec
        # Note that `attributes` is handled special and we always override it
        self.arg_spec = self.ARGUMENT
        self.option_spec = copy.deepcopy(self.OPTIONS)
        if 'attrs' in self.option_spec:  # pragma: no cover
            raise ValueError("'attrs' is a reserved option name and cannot be overriden")
        self.option_spec['attrs'] = [{}, type_html_attribute_dict]

        self._block_mgr = block_mgr
        self.length = length
        self.tracker = tracker
        self.md = block_mgr.md
        self.arguments = []
        self.options = {}
        self.config = config
        self.on_init()

    def is_raw(self, tag):
        """Is raw element."""

        return self._block_mgr.is_raw(tag)

    def is_block(self, tag):  # pragma: no cover
        """Is block element."""

        return self._block_mgr.is_block(tag)

    def html_escape(self, text):
        """Basic html escaping."""

        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text

    def dedent(self, text, length=None):
        """Dedent raw text."""

        if length is None:
            length = self.md.tab_length

        min_length = float('inf')
        for x in RE_INDENT.findall(text):
            min_length = min(len(x), min_length)
        min_length = min(min_length, length)

        return RE_DEDENT.sub(lambda m, l=min_length: '' if m.group(2) is not None else m.group(1)[l:], text)

    def on_init(self):
        """On initialize."""

    def on_markdown(self):
        """Check how element should be treated by the Markdown parser."""

        return "auto"

    def _validate(self, parent, arg, **options):
        """Parse configuration."""

        # Check argument
        if (self.arg_spec is not None and ((arg and not self.arg_spec) or (not arg and self.arg_spec))):
            return False

        self.argument = arg

        # Fill in defaults options
        spec = self.option_spec
        parsed = {}
        for k, v in spec.items():
            parsed[k] = v[0]

        # Parse provided options
        for k, v in options.items():

            # Parameter not in spec
            if k not in spec:
                # Unrecognized parameter name
                return False

            # Spec explicitly handles parameter
            else:
                parser = spec[k][1]
                if parser is not None:
                    try:
                        v = parser(v)
                    except Exception:
                        # Invalid parameter value
                        return False
            parsed[k] = v

        # Add parsed options to options
        self.options = parsed

        return self.on_validate(parent)

    def on_validate(self, parent):
        """
        Handle validation event.

        Run after config parsing completes and allows for the opportunity
        to invalidate the block if argument, options, or even the parent
        element do not meet certain criteria.

        Return `False` to invalidate the block.
        """

        return True

    @abstractmethod
    def on_create(self, parent):
        """Create the needed element and return it."""

    def _create(self, parent):
        """Create the element."""

        el = self.on_create(parent)

        # Handle general HTML attributes
        attrib = el.attrib
        for k, v in self.options['attrs'].items():
            if k == 'class':
                if k in attrib:
                    # Don't validate what the developer as already attached
                    v = type_string_delimiter(' ')(attrib['class']) + v
                attrib['class'] = ' '.join(v)
            else:
                attrib[k] = v
        return el

    def _end(self, block):
        """Reached end of the block, dedent raw blocks and call `on_end` hook."""

        mode = self.on_markdown()
        add = self.on_add(block)
        if mode == 'raw' or (mode == 'auto' and self.is_raw(add)):
            add.text = mutil.AtomicString(self.dedent(add.text))

        self.on_end(block)

    def on_end(self, block):
        """Perform any action on end."""

    def on_add(self, block):
        """
        Adjust where the content is added and return the desired element.

        Is there a sub-element where this content should go?
        This runs before processing every new block.
        """

        return block

    def on_inline_end(self, block):
        """Perform action on the block after inline parsing."""
