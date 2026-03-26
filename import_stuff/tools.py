import requests
from bs4 import BeautifulSoup
from typing import List
from llama_index.readers.web import SimpleWebPageReader


def extract_page_content(sites: List[str]) -> List[str]:
    '''
    Extract and return webpages content
    Args:
        sites: list of webpage URLs
    '''
    docs = SimpleWebPageReader(html_to_text=True).load_data(sites)
    pages_content=[doc.get_content() for doc in docs]
    return pages_content



def get_html_body(url: str):
    '''
    Extract and return webpages' html body
    Args:
        url: website URL string
    '''

    response = requests.get(url)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    body = soup.body
    
    if not body:
        return None
    
    return  str(body)