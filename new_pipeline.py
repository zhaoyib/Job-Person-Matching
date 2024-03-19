'''
@File      :   new_pipeline.py
@Time      :   2024/03/18 11:39:20
@LastEdit  :   2024/03/19 09:42:23
@Author    :   YiboZhao 
@Version   :   1.0
@Site      :   https://github.com/zhaoyib
'''
import os
import time
import torch
import pickle
import numpy
import heapq
from tqdm import tqdm
from typing import Union, List, Dict
from model.Embedder import Embedder
from model.Reranker import Reranker
from toolkits.chunk import Chunker
from toolkits.logger import logger_wrapper
from toolkits.utils import file_reader

#Define class P for the heap
#Equal to reload the "<"
class P():
    def __init__(self,a,b,c,d) -> None:
        self.a = a
        self.b = b
        self.c = c
        self.d = d
    
    def __lt__(self,other):
        if self.d < other.d:
            return True
        else:
            return False


#the Pipeline of the project.
class Pipeline():
    def __init__(self,configs:dict= {"folder_path":"C:/JPM/resume_files",
                                     "cv_embedding_bs":256,"exact_retrive":10,
                                     "embedding_path":"C:/JPM/code/embedding_files",
                                     "rough_retrive":50,"chunker_method":
                                     {"mode":"sliding_window","window_size":300,"overlap":50}}) -> None:
        '''
        init the pipeline, configs optionally include:
            folder_path     : "C:/JPM/resume_files" as default.
            cv_embedding_bs : 256 as default.
            embedding_path  : "C:/JPM/code/embedding_files" as default.
            chunker_method  : {"mode":"sliding_window","window_size":300,"overlap":50} as default.
            rough_retrive   : how many files to return in the first retrival. 50 as default.
            exact_retrive   : how many files to return in the final retrival. 10 as default.
        '''
        self.configs = configs
        self.logger = logger_wrapper()
        self.embedder = Embedder()
        self.chunker = Chunker(self.configs["chunker_method"])
        self.reranker = Reranker()
        pass

    def init_Embedding(self):
        '''
        init the embedding of cvs
        file: pkl file, will be read to a dict, key : value = cv_id_index : embedding
        '''
        folder_path = self.configs["folder_path"]
        batch_size = self.configs["cv_embedding_bs"]
        total_files = len(os.listdir(folder_path))
        res = []
        count = 0
        for batch in tqdm(file_reader(folder_path=folder_path,batch_size = batch_size),total=total_files//batch_size,desc="File Feature Extract"):
            after_chunk = {}
            for one_piece in batch.items():
                one_dict = {one_piece[0]:one_piece[1]}
                after_chunk.update(self.chunker(one_dict))
            res.extend(self.embedder.encode(after_chunk,batch_size=256,enable_tqdm=False))
            #res is a list of tuple.
            #tuple is (cv_id_index, text, embedding)
            if len(res) > 36000:
                with open(f"embedding_files/embeddings_{count}.pkl", "wb") as file:
                    pickle.dump(res, file)
                self.logger.info(f" embeddings_{str(count)}.pkl has been dumped successfully, with {str(len(res))} tuples. ")
                self.logger.info(f"VMemory used for embedding {str(torch.cuda.max_memory_allocated()/1000000)} Mb ")
                #self.logger.info(f"res with the format:{res[0]}")
                count = count + 1
                res = []
        with open(f"embedding_files/embeddings_{count}.pkl", "wb") as file:
            pickle.dump(res, file)
        self.logger.info(f" embeddings_{str(count)}.pkl has been dumped successfully, with {str(len(res))} tuples.")
        self.logger.info(f"VMemory used for embedding {str(torch.cuda.max_memory_allocated()/1000000)} Mb")

    def _load_Embeddings(self):
        '''
        return a generator of embeddings. similar to file loader.
        '''
        items = os.listdir(self.configs["embedding_path"])
        total_files = len(items)
        for i in range(total_files):
            item_path = os.path.join(self.configs["embedding_path"],items[i])
            with open(item_path,"rb") as file:
                embeddings = pickle.load(file)
            yield embeddings

    @classmethod
    def _cosine_similarity(self,array1:numpy.ndarray,array2:numpy.ndarray)->float:
        '''
        calculate the cosine similarity between two array.
        return a float.
        '''
        assert isinstance(array1,numpy.ndarray),"Please Transfer the Array1 to Numpy Ndarray"
        assert isinstance(array2,numpy.ndarray),"Please Transfer the Array2 to Numpy Ndarray"
        inner_product = numpy.dot(array1,array2)
        cos_similarity = inner_product / (numpy.linalg.norm(array1)*numpy.linalg.norm(array2))
        return cos_similarity

    def _brutal_search(self,array_q:numpy.ndarray)->list:
        '''
        brutal query method

        parameter:
            array_q  : the input query array, numpy.ndarray
        return:
            rough_res: the relevant CV, a list of tuple, (id, text, embedding, sim)
            len of the rough_res is self.configs["rough_retrive"], 50 as default.
        '''
        heap = []
        for batch in self._load_Embeddings():
            for id,text,embedding in batch:
                sim = self._cosine_similarity(embedding,array_q)
                p = P(id,text,embedding,sim)
                if(len(heap) < self.configs["rough_retrive"]):
                    heapq.heappush(heap,p)
                else:
                    tmp = heapq.heappushpop(heap,p)
        heap_list = list(heapq.heappop(heap) for _ in range(len(heap)))
        heap_list = [(item.a,item.b,item.c,item.d) for item in heap_list]
        return heap_list    
    
    def _encode_query(self,query_text:str):
        '''
        encode the query to embedding. the query no longer than 200 tokens.

        parameter: raw text of query_text, no longer than 200 tokens.
        return   : the embedding of the query_text, a numpy.ndarray with 768 dimension.
        '''
        assert len(query_text) < 200, "please reduce the length of the Job Define."
        embedding = self.embedder.encode({"qt":query_text})[0][2]
        return embedding
    
    def rough_retrieve(self,query_text:str):
        '''
        pipeline of rough retrieve, call it to rough retrieve.

        parameters:
            query_text : a str of Job Define, no longer than 200 tokens.
        return:
            CVs        : a list of CV matching the JD most, the element of list is tuple.
                         tuple: (cv_id_index, text, embedding, sim)
        '''
        q_embedding = self._encode_query(query_text)
        CVs = self._brutal_search(q_embedding)
        return CVs
    
    def exact_retrieve(self, query_text:str, CVs:list):
        '''
        pipeline of exact retrieve, call it to exact retrieve.

        parameters:
            query_text : a str of Job Define, no longer than 200 tokens.
        return:
            CVs        : a list of CV matching the JD most, the element of the list is tuple.
                         tuple: (cv_id_index, text, embedding, score), ordered by score, desc.
        '''
        rerank_res = self.reranker.rerank(query_text,CVs)
        return rerank_res
    
    def retireve(self,query):
        '''
        integrate all processes, input the query str to call it and get the res.

        parameter:
            query: a str of Job Define, no longer than 200 tokens.
        return:
            res  : a dict of res, with keys followed
                'rerank_passages': sorted_passages
                'rerank_scores'  : sorted_scores
                'rerank_ids'     : sorted_cvids
        '''
        CVs = self.rough_retrieve(query)
        res = self.exact_retrieve(query,CVs)
        return res
    


if __name__ == "__main__":
    pipline = Pipeline()
    #pipline.init_Embedding()
    res = pipline.retireve("天猫综合客服")
    res = list(zip(res['rerank_ids'],res['rerank_scores'],res['rerank_passages']))
    for person in res:
        print(person)