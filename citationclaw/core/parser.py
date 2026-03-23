import re
from bs4 import BeautifulSoup

class google_scholar_html_parser:
    def __init__(self):
        name = 'google_scholar_html_parser'

    def parse_page(self, html_text):
        """
        使用 BeautifulSoup 解析 Google Scholar 搜索结果页面。
        支持普通论文和 [引用] (citation-only) 条目。

        返回:
            tuple: (paper_dict, next_page)
        """
        # 只取"在引用文章中搜索"之后的部分（与原逻辑一致）
        if '在引用文章中搜索' in html_text:
            html_text = html_text.split('在引用文章中搜索')[-1]

        soup = BeautifulSoup(html_text, 'html.parser')
        paper_dict = {}
        next_page = 'EMPTY'

        # 查找所有搜索结果容器
        results = soup.select('div.gs_r.gs_or.gs_scl')

        for result in results:
            paper_record = {
                'paper_link': '',
                'paper_title': '',
                'paper_year': '',
                'citation': '',
                'authors': {}
            }

            # 提取标题和链接
            title_tag = result.select_one('h3.gs_rt')
            if not title_tag:
                continue

            title_link = title_tag.find('a')
            if title_link:
                # 普通论文：有链接
                paper_record['paper_link'] = title_link.get('href', '')
                paper_record['paper_title'] = title_link.get_text(strip=True)
            else:
                # [引用] 条目：无链接
                # 先移除 gs_ctu span（如 [引用][C]），再取剩余文本作为标题
                ctu_span = title_tag.find('span', class_='gs_ctu')
                if ctu_span:
                    ctu_span.decompose()
                paper_record['paper_title'] = title_tag.get_text(strip=True)

            if not paper_record['paper_title']:
                continue

            # 提取作者（有 Google Scholar 主页链接的作者）
            author_links = result.select('div.gs_a a[href*="citations?"]')
            for i, author_a in enumerate(author_links):
                author_name = author_a.get_text(strip=True)
                author_href = author_a.get('href', '')
                if author_href and not author_href.startswith('http'):
                    author_href = f'https://scholar.google.com{author_href}'
                paper_record['authors'][f'author_{i}_{author_name}'] = author_href

            # 提取引用次数
            cite_links = result.select('div.gs_fl a[href*="cites="]')
            for cite_link in cite_links:
                cite_text = cite_link.get_text(strip=True)
                if cite_text:
                    paper_record['citation'] = cite_text
                    break

            # ---- 年份提取----
            pat_strict = re.compile(r',\s*((?:19|20)\d{2})\s*-')  # 更精确：", 2023 -"
            pat_loose = re.compile(r'\b(?:19|20)\d{2}\b')  # 兜底：任意 19xx/20xx
            gs_a = result.select_one('div.gs_a')
            year = ''
            if gs_a:
                meta_text = gs_a.get_text(" ", strip=True)

                m = pat_strict.search(meta_text)
                if m:
                    year =  int(m.group(1))
                else:
                    m = pat_loose.search(meta_text)
                    year = int(m.group(0)) if m else None
            paper_record['paper_year'] = year

            paper_id = len(paper_dict)
            paper_dict[f'paper_{paper_id}'] = paper_record


        # 查找下一页链接
        # 方法1: 包含 nav_next 的 span 的父 <a>
        next_span = soup.find('span', class_='gs_ico_nav_next')
        next_link = next_span.find_parent('a') if next_span else None

        # 方法2: 包含"下一页"文本的 <a>
        if not next_link:
            for a_tag in soup.find_all('a'):
                if '下一页' in a_tag.get_text():
                    next_link = a_tag
                    break

        if next_link:
            href = next_link.get('href', '')
            if href:
                if not href.startswith('http'):
                    next_page = f'https://scholar.google.com{href}'
                else:
                    next_page = href

        return paper_dict, next_page

    def extract_structure_data(self,html_text):
        """
        从 HTML 文本中提取所有完整的 <a> 标签及其内容

        参数:
            html_text (str): 包含 HTML 内容的文本

        返回:
            list: 所有匹配到的 <a> 标签列表，无匹配则返回空列表
        """
        html_text = html_text.split('在引用文章中搜索')[-1]
        # 定义匹配 <a> 标签的正则表达式
        pattern = r'<a\b[^>]*>.*?</a>'

        # 使用 re.DOTALL 标志，让 . 可以匹配换行符（处理跨行吗标签）
        # 使用 re.IGNORECASE 标志，匹配大小写不敏感的 <A> 标签
        matches = re.findall(pattern, html_text, re.DOTALL | re.IGNORECASE)

        return matches

    def extract_paper_link(self,html_text):
        match = re.search(r'href="(.*?)"', html_text)
        match = match.group(1)
        match = match.replace('&amp;','&')
        return match

    def extract_paper_title(self,html_text):
        match = re.search(r'>(.*?)</a>', html_text)
        match = match.group(1)
        return match

    def extract_cite(self,html_text):
        match = re.search(r'>(.*?)</a>', html_text)
        match = match.group(1)
        return match

    def extract_author_link(self,html_text):
        match = re.search(r'href="(.*?)"', html_text)
        match = match.group(1)
        match = match.replace('&amp;', '&')
        return match

    def extract_next_page(self,html_text):
        match = re.search(r'href="(.*?)"', html_text)
        match = match.group(1)
        match = match.replace('&amp;', '&')
        return match

    def parsing_this_page(self,structure_data):
        complete_next_page = 'EMPTY'
        paper_dict = {}
        paper_content_record = {
            'paper_link': '',
            'paper_title': '',
            'citation': '',
            'authors': {}
        }
        for line_id,line in enumerate(structure_data):
            if '<a id=' in line:
                ## 先保存上一篇的内容
                if paper_content_record['paper_link'] != '' and paper_content_record['paper_title'] != '':
                    paper_id = len(paper_dict)
                    paper_dict[f'paper_{paper_id}'] = paper_content_record
                    paper_content_record = {
                        'paper_link': '',
                        'paper_title': '',
                        'citation': '',
                        'authors': {}
                    }

                ## 提取当前paper的信息
                paper_link = self.extract_paper_link(line)
                paper_title = self.extract_paper_title(line)
                paper_content_record['paper_link'] = paper_link
                paper_content_record['paper_title'] = paper_title
            elif 'citations?' in line:
                author_name = self.extract_paper_title(line)
                author_link = self.extract_author_link(line)
                complete_author_link = f'https://scholar.google.com{author_link}'
                author_id = len(paper_content_record['authors'])
                paper_content_record['authors'][f'author_{author_id}_{author_name}'] = complete_author_link
            elif ('cites=' in line and 'nav_next' not in line) or '被引用次数' in line:
                cite_str = self.extract_cite(line)
                if paper_content_record['citation'] == '':
                    paper_content_record['citation'] = cite_str
            elif 'nav_next' in line or '下一页' in line:  ##下一页的链接
                next_page = self.extract_next_page(line)
                complete_next_page = f'https://scholar.google.com{next_page}'

            ## 记录最后一篇
            if line_id == len(structure_data) - 1:
                if paper_content_record['paper_link'] != '' and paper_content_record['paper_title'] != '':
                    paper_id = len(paper_dict)
                    paper_dict[f'paper_{paper_id}'] = paper_content_record

        return paper_dict, complete_next_page
