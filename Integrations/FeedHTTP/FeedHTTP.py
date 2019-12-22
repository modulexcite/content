import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *

''' IMPORTS '''
import urllib3
import itertools
import requests

# disable insecure warnings
urllib3.disable_warnings()

SOURCE_NAME = demisto.params().get('source_name')


class Client(object):
    def __init__(self, url: str, insecure: bool = False, credentials: dict = None, ignore_regex: str = None,
                 encoding: str = None, indicator: str = None, fields: str = '{}', polling_timeout: int = 20,
                 user_agent: str = None, **kwargs):
        """Implements class for miners of plain text feeds over http/https.
        **Config parameters**
        :param: url: URL of the feed.
        :param: polling_timeout: timeout of the polling request in seconds.
            Default: 20
        :param: verify_cert: boolean, if *true* feed HTTPS server certificate is
            verified. Default: *true*
        :param: user_agent: string, value for the User-Agent header in HTTP
            request. If ``MineMeld``, MineMeld/<version> is used.
            Default: python ``requests`` default.
        :param: ignore_regex: Python regular expression for lines that should be
            ignored. Default: *null*
        :param: indicator: an *extraction dictionary* to extract the indicator from
            the line. If *null*, the text until the first whitespace or newline
            character is used as indicator. Default: *null*
        :param: fields: a dicionary of *extraction dictionaries* to extract
            additional attributes from each line. Default: {}
        :param: encoding: encoding of the feed, if not UTF-8. See
            ``str.decode`` for options. Default: *null*, meaning do
            nothing, (Assumes UTF-8).
        **Extraction dictionary**
            Extraction dictionaries contain the following keys:
            :regex: Python regular expression for searching the text.
            :transform: template to generate the final value from the result
                of the regular expression. Default: the entire match of the regex
                is used as extracted value.
            See Python `re <https://docs.python.org/2/library/re.html>`_ module for
            details about Python regular expressions and templates.
        Example:
            Example config in YAML where extraction dictionaries are used to
            extract the indicator and additional fields::
                url: https://www.dshield.org/block.txt
                ignore_regex: "[#S].*"
                indicator:
                    regex: '^([0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3})\\t([0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3})'
                    transform: '\\1-\\2'
                fields:
                    dshield_nattacks:
                        regex: '^.*\\t.*\\t[0-9]+\\t([0-9]+)'
                        transform: '\\1'
                    dshield_name:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t([^\\t]+)'
                        transform: '\\1'
                    dshield_country:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t[^\\t]+\\t([A-Z]+)'
                        transform: '\\1'
                    dshield_email:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t[^\\t]+\\t[A-Z]+\\t(\\S+)'
                        transform: '\\1'
            Example config in YAML where the text in each line until the first
            whitespace is used as indicator::
                url: https://ransomwaretracker.abuse.ch/downloads/CW_C2_URLBL.txt
                ignore_regex: '^#'
        Args:
            name (str): node name, should be unique inside the graph
            chassis (object): parent chassis instance
            config (dict): node config.
        """
        self.url = url
        self.polling_timeout = int(polling_timeout)
        self.verify_cert = not insecure
        self.user_agent = user_agent
        self.encoding = encoding

        if not credentials:
            credentials = {}
        self.username = credentials.get('identifier', None)
        self.password = credentials.get('password', None)

        self.ignore_regex = ignore_regex
        if self.ignore_regex is not None:
            self.ignore_regex = re.compile(self.ignore_regex)

        self.indicator = indicator
        if self.indicator is not None:
            self.indicator = json.loads(self.indicator)
            if 'regex' in self.indicator:
                self.indicator['regex'] = re.compile(self.indicator['regex'])
            else:
                raise ValueError(f'{SOURCE_NAME} - indicator stanza should have a regex')
            if 'transform' not in self.indicator:
                if self.indicator['regex'].groups > 0:
                    LOG(f'{SOURCE_NAME} - no transform string for indicator but pattern contains groups')
                self.indicator['transform'] = r'\g<0>'

        self.fields = json.loads(fields)
        for f, fattrs in self.fields.items():
            if 'regex' in fattrs:
                fattrs['regex'] = re.compile(fattrs['regex'])
            else:
                raise ValueError(f'{SOURCE_NAME} - {f} field does not have a regex')
            if 'transform' not in fattrs:
                if fattrs['regex'].groups > 0:
                    LOG(f'{SOURCE_NAME} - no transform string for field {f} but pattern contains groups')
                fattrs['transform'] = r'\g<0>'

    def build_iterator(self):
        rkwargs = dict(
            stream=True,
            verify=self.verify_cert,
            timeout=self.polling_timeout
        )

        if self.user_agent is not None:
            rkwargs['headers'] = {
                'User-Agent': self.user_agent
            }

        if self.username is not None and self.password is not None:
            rkwargs['auth'] = (self.username, self.password)

        r = requests.get(
            self.url,
            **rkwargs
        )
        try:
            r.raise_for_status()
        except Exception:
            LOG(f'{SOURCE_NAME} - exception in request: {r.status_code} {r.content}')
            raise

        result = r.iter_lines()
        if self.encoding is not None:
            result = map(
                lambda x: x.decode(self.encoding).encode('utf_8'),
                result
            )
        else:
            result = map(
                lambda x: x.decode('utf_8'),
                result
            )
        if self.ignore_regex is not None:
            result = filter(
                lambda x: self.ignore_regex.match(x) is None,
                result
            )
        return result


# simple function to iterate list in batches
def batch(iterable, batch_size=1):
    current_batch = []
    for item in iterable:
        current_batch.append(item)
        if len(current_batch) == batch_size:
            yield current_batch
            current_batch = []
    if current_batch:
        yield current_batch


def test_module(client, args):
    client.build_iterator()
    return 'ok', {}, {}


def fetch_indicators_command(client, itype):
    iterator = client.build_iterator()
    indicators = []
    for line in iterator:
        line = line.strip()
        if not line:
            continue

        if client.indicator is None:
            indicator = line.split()[0]

        else:
            indicator = client.indicator['regex'].search(line)
            if indicator is None:
                continue

            indicator = indicator.expand(client.indicator['transform'])

        attributes = {}
        for f, fattrs in client.fields.items():
            m = fattrs['regex'].search(line)

            if m is None:
                continue

            attributes[f] = m.expand(fattrs['transform'])

            try:
                i = int(attributes[f])
            except Exception:
                pass
            else:
                attributes[f] = i

        # return [[indicator, attributes]]

        attributes['value'] = value = indicator
        attributes['type'] = itype
        indicators.append({
            "value": value,
            "type": itype,
            "rawJSON": attributes,
        })
    return indicators


def get_indicators(client, args):
    indicators = fetch_indicators_command(client, demisto.params().get('indicator_type'))
    return '', {}, indicators


def main():
    # Write configure here
    params = {k: v for k, v in demisto.params().items() if v is not None}
    handle_proxy()
    client = Client(**params)
    command = demisto.command()
    demisto.info('Command being called is {}'.format(command))
    # Switch case
    commands = {
        'test-module': test_module,
        'get-indicators': get_indicators
    }
    try:
        if demisto.command() == 'fetch-indicators':
            indicators = fetch_indicators_command(client, params.get('indicator_type'))
            # we submit the indicators in batches
            for b in batch(indicators, batch_size=2000):
                demisto.createIndicators(b)
        else:
            readable_output, outputs, raw_response = commands[command](client, demisto.args())
            return_outputs(readable_output, outputs, raw_response)
    except Exception as e:
        err_msg = f'Error in {SOURCE_NAME} Integration [{e}]'
        return_error(err_msg)


if __name__ == '__builtin__' or __name__ == 'builtins':
    main()