# ruff: noqa: E501  —— 内置语料是中文长句数据，不适用行长规则
"""可控 chunk 策略 A/B 基准（内置合成语料）。

用途：验证评估链路，并对比「旧策略：100 字符硬切」vs「新策略：500 字符递归切」
在检索指标上的差异。

⚠️ 说明：
  - 语料是内置的合成中文资料，不是任何人的真实私有数据；
  - 结论只对这套语料负责，不能外推到你的真实知识库；
  - 要评估真实效果，请用 scripts/evaluate.py + 你自己的资料与问题集。

运行：python scripts/benchmark_chunking.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.embeddings.embedder import Embedder  # noqa: E402
from app.rag.splitter import TextSplitter  # noqa: E402
from app.storage.vector_store import Chunk, VectorStore, make_chunk_id  # noqa: E402

# ---------------- 合成语料（3 份文档，各含若干独立事实） ----------------
CORPUS = [
    (
        "数据结构.txt",
        """链表是一种线性数据结构，由一系列节点组成，每个节点包含数据域和指针域，指针指向下一个节点，因此链表支持 O(1) 时间在任意位置插入和删除节点。与数组不同，链表不支持随机访问，查找第 i 个元素需要从头遍历。
栈是一种后进先出的线性表，只能在栈顶进行插入和删除操作，插入称为入栈，删除称为出栈，常用于函数调用、括号匹配和表达式求值等场景。
队列是一种先进先出的线性表，允许在队尾插入元素、在队头删除元素，常用于任务调度和广度优先搜索。
二叉树是每个节点最多有两个子节点的树结构，其遍历方式主要有前序遍历、中序遍历和后序遍历三种，分别对应根节点的访问顺序。
二叉搜索树是一种特殊的二叉树，左子树所有节点的值都小于根节点，右子树所有节点的值都大于根节点，因此中序遍历会得到一个有序序列。
冒泡排序通过相邻元素两两比较并交换，每一轮把最大的元素冒泡到末尾，平均和最坏时间复杂度都是 O(n^2)，是一种稳定的排序算法。
快速排序通过选择一个基准元素，把序列分成小于和大于基准的两部分，再递归排序，平均时间复杂度是 O(n log n)。
哈希表通过哈希函数把键映射到数组下标，查找插入删除的平均时间复杂度都是 O(1)，冲突的常见解决方法是链地址法和开放定址法。
图的表示方法主要有邻接矩阵和邻接表两种，邻接矩阵适合稠密图，邻接表适合稀疏图，图的遍历有深度优先搜索和广度优先搜索两种。
堆是一种完全二叉树，分为大顶堆和小顶堆，大顶堆的堆顶是最大值，常用于实现优先队列和堆排序。""",
    ),
    (
        "计算机网络.txt",
        """TCP 是一种面向连接的可靠传输协议，通过三次握手建立连接、四次挥手释放连接，提供可靠、有序、面向字节流的传输服务，常用于需要保证数据不丢失的场景。
三次握手的过程是：客户端发送 SYN 报文，服务器回复 SYN+ACK 报文，客户端再回复 ACK 报文，连接建立完成。
UDP 是一种无连接的传输协议，不保证可靠交付，没有拥塞控制和流量控制，但开销小、速度快，常用于视频直播和实时游戏。
HTTP 是超文本传输协议，基于请求-响应模型，常见的请求方法有 GET、POST、PUT、DELETE，其中 GET 用于获取资源，POST 用于提交数据。
HTTPS 在 HTTP 的基础上加入了 TLS 加密层，通过数字证书验证服务器身份，并对传输的数据进行加密，默认端口是 443。
DNS 是域名系统，负责把人类可读的域名转换成 IP 地址，采用层次化的树形结构，查询过程通常先查本地缓存，再向根域名服务器递归查询。
IP 地址是网络层的逻辑地址，IPv4 是 32 位的地址，通常用点分十进制表示，例如 192.168.1.1，用于在互联网中唯一标识一台主机。
路由器工作在网络层，根据目的 IP 地址和路由表转发数据包，是连接不同网络的关键设备。
交换机工作在数据链路层，根据 MAC 地址转发以太网帧，用于在局域网内连接多台主机。
HTTP 常见的状态码有 200 表示成功，404 表示资源未找到，500 表示服务器内部错误，301 和 302 表示重定向。""",
    ),
    (
        "公司制度.txt",
        """公司的带薪年假制度规定，每位正式员工每年享有五天带薪年假，入职满一年后即可申请，年假需提前三天向直属主管提交申请。
公司实行弹性工作制，核心工作时间为上午十点到下午四点，其余时间员工可根据个人情况灵活安排。
员工出差产生的交通费和住宿费可以申请报销，需在出差结束后两周内提交发票和出差申请单，经部门主管审批后由财务统一打款。
公司为每位员工缴纳五险一金，包括养老保险、医疗保险、失业保险、工伤保险、生育保险和住房公积金。
新员工入职后有三个月的试用期，试用期内表现良好的可以提前转正，转正后享受完整的薪资和福利待遇。
公司每年组织一次全体员工的年度体检，体检费用由公司全额承担，员工可在指定体检机构预约。
员工如需请假，一天以内的病假或事假需提前一天向直属主管报备，三天以上的假期需部门经理审批。
公司的绩效考核每半年进行一次，考核结果与年终奖和调薪直接挂钩，连续两次考核优秀可获得晋升机会。
公司提供免费的下午茶和加班餐，员工在工作日晚六点后加班可申请加班餐。
公司支持员工参加外部培训，培训费用在五千元以内的可全额报销，超过部分需提前报备审批。""",
    ),
]

# ---------------- 评估问题：每条对应一个来源文档 + 一个答案关键词 ----------------
QUESTIONS = [
    {"question": "链表为什么不能随机访问？", "source": "数据结构.txt", "keyword": "指针域"},
    {"question": "栈的插入操作叫什么？", "source": "数据结构.txt", "keyword": "入栈"},
    {"question": "队列在哪个位置插入元素？", "source": "数据结构.txt", "keyword": "队尾"},
    {"question": "二叉树有哪几种遍历方式？", "source": "数据结构.txt", "keyword": "前序遍历"},
    {"question": "二叉搜索树中序遍历得到什么？", "source": "数据结构.txt", "keyword": "有序序列"},
    {"question": "冒泡排序的时间复杂度是多少？", "source": "数据结构.txt", "keyword": "O(n^2)"},
    {"question": "快速排序怎么划分序列？", "source": "数据结构.txt", "keyword": "基准元素"},
    {"question": "哈希冲突怎么解决？", "source": "数据结构.txt", "keyword": "链地址法"},
    {"question": "稀疏图适合用哪种表示？", "source": "数据结构.txt", "keyword": "邻接表"},
    {"question": "大顶堆的堆顶是什么？", "source": "数据结构.txt", "keyword": "大顶堆"},
    {"question": "TCP 建立连接要几次握手？", "source": "计算机网络.txt", "keyword": "三次握手"},
    {"question": "UDP 有连接吗？", "source": "计算机网络.txt", "keyword": "无连接"},
    {"question": "HTTP 获取资源用哪个方法？", "source": "计算机网络.txt", "keyword": "GET"},
    {"question": "HTTPS 默认端口是多少？", "source": "计算机网络.txt", "keyword": "443"},
    {"question": "DNS 负责什么？", "source": "计算机网络.txt", "keyword": "域名"},
    {"question": "IPv4 地址是多少位？", "source": "计算机网络.txt", "keyword": "32 位"},
    {"question": "路由器工作在哪一层？", "source": "计算机网络.txt", "keyword": "网络层"},
    {"question": "交换机根据什么转发帧？", "source": "计算机网络.txt", "keyword": "MAC 地址"},
    {"question": "状态码 404 表示什么？", "source": "计算机网络.txt", "keyword": "资源未找到"},
    {"question": "员工每年有几天带薪年假？", "source": "公司制度.txt", "keyword": "五天带薪年假"},
    {"question": "核心工作时间是几点到几点？", "source": "公司制度.txt", "keyword": "上午十点"},
    {"question": "出差报销要多久内提交？", "source": "公司制度.txt", "keyword": "两周内"},
    {"question": "五险一金包括什么？", "source": "公司制度.txt", "keyword": "住房公积金"},
    {"question": "新员工试用期多久？", "source": "公司制度.txt", "keyword": "三个月"},
    {"question": "绩效考核多久一次？", "source": "公司制度.txt", "keyword": "每半年"},
    {"question": "加班餐什么时候可申请？", "source": "公司制度.txt", "keyword": "晚六点"},
    {"question": "培训费多少以内全额报销？", "source": "公司制度.txt", "keyword": "五千元"},
]


def char_chunk(text: str, chunk_size: int = 100, overlap: int = 20) -> list[str]:
    """旧策略：按字符硬切（对应重构前的 ingest.chunk_text）。"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def build_index(strategy: str, embedder: Embedder) -> VectorStore:
    store = VectorStore(embedder)
    for source, text in CORPUS:
        if strategy == "old":
            pieces = char_chunk(text, 100, 20)
            chunks = [
                Chunk(
                    id=make_chunk_id(source, None, i),
                    text=p,
                    source=source,
                    chunk_index=i,
                    metadata={"source": source},
                )
                for i, p in enumerate(pieces)
            ]
        else:
            from app.rag.loader import Segment

            chunks = TextSplitter(500, 100).split([Segment(text=text, source=source)])
        store.add(chunks)
    return store


def evaluate(store: VectorStore, top_k: int = 3) -> dict:
    hit = 0
    mrr_sum = 0.0
    coverage = 0
    for q in QUESTIONS:
        results = store.search(q["question"], top_k=top_k, threshold=0.0)
        sources = [r.chunk.source for r in results]
        texts = " ".join(r.chunk.text for r in results)

        if any(s == q["source"] for s in sources):
            hit += 1
        for rank, s in enumerate(sources, start=1):
            if s == q["source"]:
                mrr_sum += 1.0 / rank
                break
        if q["keyword"] in texts:
            coverage += 1

    n = len(QUESTIONS)
    return {
        "chunks": len(store),
        "hit_at_k": hit / n,
        "mrr_at_k": mrr_sum / n,
        "answer_coverage": coverage / n,
    }


def main() -> None:
    top_k = 3
    embedder = Embedder(os.getenv("OPENAI_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))

    print(f"评估问题 {len(QUESTIONS)} 条，Top-K={top_k}\n")
    print(f"{'策略':<28}{'块数':<8}{'Hit@K':<10}{'MRR@K':<10}{'答案覆盖@K':<10}")

    old = evaluate(build_index("old", embedder), top_k)
    new = evaluate(build_index("new", embedder), top_k)
    print(
        f"{'旧：100字符硬切':<28}{old['chunks']:<8}{old['hit_at_k']:<10.4f}{old['mrr_at_k']:<10.4f}{old['answer_coverage']:<10.4f}"
    )
    print(
        f"{'新：500字符递归切':<28}{new['chunks']:<8}{new['hit_at_k']:<10.4f}{new['mrr_at_k']:<10.4f}{new['answer_coverage']:<10.4f}"
    )


if __name__ == "__main__":
    main()
