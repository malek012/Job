from html.parser import HTMLParser

class HTMLValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_tags = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if self.open_tags and self.open_tags[-1] == tag:
            self.open_tags.pop()
        else:
            self.errors.append(f'Unclosed tag: {tag}, expected: {self.open_tags[-1] if self.open_tags else "none"}')

parser = HTMLValidator()
with open('index.html', 'r') as f:
    parser.feed(f.read())

if parser.errors:
    for e in parser.errors:
        print(f'ERROR: {e}')
else:
    print('HTML structure is valid!')

print(f'Unclosed tags: {parser.open_tags}')