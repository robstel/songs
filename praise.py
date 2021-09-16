import re
import requests
import webbrowser
from datetime import datetime
from xml.etree.ElementTree import Element, ElementTree, SubElement
from bs4 import BeautifulSoup

import config

# Extracts a verse number and text.
re_verse_number = re.compile(r'^(\d)*\.?\s*(.*)')
# Matches unnecessary content in copyright notices, including anything in
# parentheses.
re_copyright_fluff = re.compile(r'(\([^)]*\)|all rights reserved|used by permission|\.)', re.IGNORECASE)

re_author_fluff = re.compile(r'\b(words|music)\b', re.IGNORECASE)


class Praise():
    def __init__(self) -> None:
        self.session = requests.Session()

    def login(self, username, password):

        # Get the login page and extract the login nonce.
        response = self.session.get('https://www.praise.org.uk/my-account/')
        soup = BeautifulSoup(response.text, 'html.parser')
        nonce = soup.find('input', id='woocommerce-login-nonce').attrs['value']

        # Post the login credentials to the login page.
        credentials = {
            'username': username,
            'password': password,
            'woocommerce-login-nonce': nonce,
            'login': 'Log+in'
        }
        self.session.post('https://www.praise.org.uk/my-account/', credentials)

    def get_song(self, song_num: str) -> str:

        # Send a search request for the song.
        response = self.session.get(f'https://www.praise.org.uk/?s={song_num}&post_type=hymn')
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract the first search result.
        url = soup.select_one('table.search-results tr td a')['href']

        # Get the song page.
        response = self.session.get(url)
        self._convert_song(song_num, response.text)

        return url

    def _convert_song(self, song_num: str, html: str):

        soup = BeautifulSoup(html, 'html.parser')

        title_tag = soup.select_one('div.main-content div.textual h2')
        title = title_tag.string
        print(f"Title: {title}")

        related = soup.find('h2', string='Related Information')
        author_label = related.find_next('strong', string='Author:')
        authors = []
        if author_label is not None:
            authors = author_label.find_next_sibling('a').string.split(',')
        else:
            author_label = related.find_next('strong', string='Authors:')
            if author_label is not None:
                authors = [a.string for a in author_label.find_next_siblings('a')]

        themes = []
        themes_label = related.find_next('strong', string='Themes:')
        if themes_label is not None:
            themes = [a.string for a in themes_label.find_next_siblings('a')]

        copyright_label = related.find_next('strong', string='Copyright:')
        if copyright_label is None:
            copyright = 'Public Domain'
        else:
            copyright = copyright_label.next_sibling.string
            copyright = re_copyright_fluff.sub('', copyright).strip().title()

        # Start constructing the output XML.
        root_el = Element('song', {
            'xmlns': 'http://openlyrics.info/namespace/2009/song',
            'version': '0.8',
            'createdIn': 'RobStel Praise! Downloader',
            'modifiedDate': datetime.now().replace(microsecond=0).isoformat()
        })
        root_el.text = '\n'
        tree = ElementTree(root_el)
        properties_el = self._create_element(root_el, 'properties')
        titles_el = self._create_element(properties_el, 'titles')
        self._create_element(titles_el, 'title', title)
        self._create_element(properties_el, 'copyright', copyright)
        verse_order_el = self._create_element(properties_el, 'verseOrder')
        authors_el = self._create_element(properties_el, 'authors')

        for author in authors:
            # Don't use the author text if there is is more than just a name.
            if re_author_fluff.search(author) is not None:
                self._create_element(authors_el, 'author')
                continue
            # Remove dates
            author = re.sub(r'\d+-?\d+$', '', author).strip()
            # rearrange "surname, forename"
            if author.count(',') == 1:
                surname, forename = author.split(',')
                author = forename.lstrip() + ' ' + surname.rstrip()
            self._create_element(authors_el, 'author', author)

        if len(themes):
            themes_el = self._create_element(properties_el, 'themes')
            for theme in themes:
                self._create_element(themes_el, 'theme', theme)

        songbooks_el = self._create_element(properties_el, 'songbooks')
        SubElement(songbooks_el, 'songbook', {'name': 'Praise!', 'entry': song_num}).tail = '\n'

        lyrics_el = self._create_element(root_el, 'lyrics')
        verses = title_tag.find_next_siblings('p')
        self._get_verses(verses, lyrics_el, verse_order_el)

        # Write the output XML file.
        filename = song_num + '_' + re.sub(r'\W', '_', title) + '.xml'
        tree.write(filename, encoding='unicode', xml_declaration=True)
        print(filename)

    def _create_element(self, parent: Element, tag: str, text='\n') -> Element:
        element = SubElement(parent, tag)
        element.text = text
        element.tail = '\n'
        return element

    def _get_verses(self, verses, lyrics_el, verse_order_el):
        bridge_num = 0
        chorus_num = 0
        verse_order = []
        for verse_idx, verse in enumerate(verses):

            lines = [line for line in verse.stripped_strings]
            if len(lines) == 0:
                continue

            if verse_idx == 0:
                # Praise! doesn't number the first verse.
                verse_num = 1
            else:
                # Split the verse number from the first line of the verse.
                verse_num, lines[0] = re_verse_number.search(lines[0]).groups()

            is_chorus = False
            if verse_num:
                # It's a verse.
                name = f'v{verse_num}'
            elif lines[0].lower() == 'bridge:':
                # It's a bridge.
                lines = lines[1:]
                bridge_num += 1
                name = name = f'b{bridge_num}'
            else:
                # It's a chorus.
                # Is it a chorus repeat?
                if lines[0].endswith('…') and chorus_num:
                    # Just add the previous chorus to the verse order.
                    verse_order.append(f'c{chorus_num}')
                    # Deal with cases where the chorus repeat is at the start
                    # of a verse paragraph.
                    lines = lines[1:]
                    if len(lines) == 0:
                        # Done with this verse.
                        continue
                    verse_num, lines[0] = re_verse_number.search(lines[0]).groups()
                    if verse_num is not None:
                        # Process as a verse.
                        name = f'v{verse_num}'
                    elif lines[0].lower() == 'bridge:':
                        # Process as a bridge.
                        lines = lines[1:]
                        bridge_num += 1
                        name = f'b{bridge_num}'
                    else:
                        continue
                else:
                    # It's a full chorus.
                    if lines[0].lower() == 'chorus:':
                        lines = lines[1:]
                    is_chorus = True
                    chorus_num += 1
                    name = f'c{chorus_num}'

            verse_el = self._create_element(lyrics_el, 'verse')
            verse_el.set('name', name)
            verse_order.append(name)

            lines_el = self._create_element(verse_el, 'lines')

            br_el = None
            for line_idx, line in enumerate(lines):
                if line_idx == 0:
                    # Add the first line as the <lines> element text.
                    lines_el.text = line
                else:
                    # For subsequent lines, add a <br/> element with the line
                    # as the tail.
                    br_el = SubElement(lines_el, 'br')
                    br_el.tail = line

            # For a chorus, add {it} at the start of the first line and {/it}
            # at the end of the last line.
            if is_chorus:
                lines_el.text = '{it}' + lines_el.text
                if br_el is None:
                    lines_el.text += '{/it}'
                else:
                    br_el.tail += '{/it}'

        verse_order_el.text = ' '.join(verse_order)


if __name__ == '__main__':

    praise = Praise()
    praise.login(config.USERNAME, config.PASSWORD)

    while True:
        song_num = input('Enter the song number (blank to quit): ')
        if not song_num.strip():
            break
        url = praise.get_song(song_num)
        webbrowser.open_new_tab(url)
