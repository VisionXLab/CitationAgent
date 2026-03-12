import json
import pandas as pd
from pathlib import Path
from typing import Callable

class ResultExporter:
    def __init__(self, log_callback: Callable):
        """
        结果导出器

        Args:
            log_callback: 日志回调函数
        """
        self.log_callback = log_callback

    def highligh_renowned_scholar(self,flattened,renowned_scholar_excel_outputs):
        ## 标记学者
        def tag_scholar(df):
            for i in range(len(df)):
                title = df.loc[i, 'Title']
                if not pd.isnull(title):
                    if 'Fellowship' not in title:
                        if '中国科学院院士' in title or '中国工程院院士' in title or '两院院士' in title:
                            df.at[i, '两院院士/其他院士/Fellow'] = '院士'
                        elif '院士' in title:
                            df.at[i, '两院院士/其他院士/Fellow'] = '其他院士'
                        elif 'Fellow' in title or 'fellow' in title:
                            df.at[i, '两院院士/其他院士/Fellow'] = 'Fellow'
            return df

        ## 转换df，找到大佬级别
        scholar_df = []
        for d in flattened:
            # 自引论文不纳入知名学者统计
            if d.get('Is_Self_Citation', False):
                continue
            paper_title = d['Paper_Title']
            paper_year = d['Paper_Year']
            paper_link = d['Paper_Link']
            paper_citation = d['Citations']
            formated_renowned_scholars = d.get('Formated Renowned Scholar', [])
            for scholar in formated_renowned_scholars:
                name = scholar['姓名']
                if name != '':
                    scholar_df.append({
                        'Name': name,
                        'Institution': scholar['机构'],
                        'Country': scholar['国家'],
                        'Job': scholar['职务'],
                        'Title': scholar['荣誉称号'],
                        'PaperTitle': paper_title,
                        'PaperCitations': paper_citation,
                        'PaperYear': paper_year,
                        'PaperLink': paper_link,
                        'CitingPaper': d.get('Citing_Paper', ''),
                        '两院院士/其他院士/Fellow': ''
                    })

        scholar_df = pd.DataFrame(scholar_df)
        scholar_df = tag_scholar(scholar_df)

        selected_df = scholar_df[scholar_df['两院院士/其他院士/Fellow'] != ''].reset_index(drop=True)

        scholar_df.to_excel(renowned_scholar_excel_outputs[0], sheet_name='All Renowned scholars', index=False)
        selected_df.to_excel(renowned_scholar_excel_outputs[1], sheet_name='Top-tier scholars', index=False)

    def export(
        self,
        input_file: Path,
        excel_output: Path,
        json_output: Path
    ):
        """
        导出结果为Excel和JSON格式

        Args:
            input_file: 输入JSONL文件(来自author_searcher)
            excel_output: 输出Excel文件路径
            json_output: 输出JSON文件路径
        """
        self.log_callback("正在加载数据...")

        # 读取JSONL文件
        with open(input_file, 'r', encoding='utf-8') as f:
            data = [json.loads(line) for line in f]

        # 展平数据结构
        flattened = []
        for line in data:
            for _, content in line.items():
                flattened.append(content)

        self.log_callback(f"共 {len(flattened)} 条记录")

        # 确保输出目录存在
        excel_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.parent.mkdir(parents=True, exist_ok=True)

        # 导出Excel
        self.log_callback("正在生成Excel文件...")
        try:
            df = pd.DataFrame(flattened)
            df.to_excel(excel_output, index=False)
            self.log_callback(f"Excel文件已保存: {excel_output}")
        except Exception as e:
            self.log_callback(f"Excel导出失败: {e}")
            raise

        # ## 高亮重量级学者
        renowned_scholar_excel_output1 = excel_output.with_stem(excel_output.stem + "_all_renowned_scholar")
        renowned_scholar_excel_output1.parent.mkdir(parents=True, exist_ok=True)
        renowned_scholar_excel_output2 = excel_output.with_stem(excel_output.stem + "_top-tier_scholar")
        renowned_scholar_excel_output2.parent.mkdir(parents=True, exist_ok=True)
        renowned_scholar_excel_outputs = [renowned_scholar_excel_output1,renowned_scholar_excel_output2]
        self.log_callback("正在生成重量级学者Excel文件...")
        try:
            self.highligh_renowned_scholar(flattened,renowned_scholar_excel_outputs)
            self.log_callback(f"重量级学者Excel文件已保存: {renowned_scholar_excel_outputs}")
        except Exception as e:
            self.log_callback(f"重量级学者Excel导出失败: {e}")
            raise

        # 导出JSON
        self.log_callback("正在生成JSON文件...")
        try:
            with open(json_output, 'w', encoding='utf-8') as f:
                json.dump(flattened, f, ensure_ascii=False, indent=3)
            self.log_callback(f"JSON文件已保存: {json_output}")
        except Exception as e:
            self.log_callback(f"JSON导出失败: {e}")
            raise

        self.log_callback("导出完成!")
