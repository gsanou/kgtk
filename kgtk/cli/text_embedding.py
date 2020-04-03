import sys
import typing

class EmbeddingVector:
    def __init__(self, model_name=None):
        from sentence_transformers import SentenceTransformer,  SentencesDataset, LoggingHandler, losses, models
        import logging
        from collections import defaultdict
        if model_name is None:
            model_name = 'bert-base-nli-mean-tokens'
        # xlnet need to be trained before using, we can't use this for now
        # elif model_name == "xlnet-base-cased":
        #     word_embedding_model = models.XLNet('xlnet-base-cased')
        # # Apply mean pooling to get one fixed sized sentence vector
        #     pooling_model = models.Pooling(word_embedding_model.get_word_embedding_dimension(),
        #                                pooling_mode_mean_tokens=True,
        #                                pooling_mode_cls_token=False,
        #                                pooling_mode_max_tokens=False)
        #     self.model = SentenceTransformer(modules=[word_embedding_model, pooling_model])
        else:
            self.model_name = model_name
            self.model = SentenceTransformer(model_name)
        self.q_nodes_descriptions = dict()
        self.vectors_map = dict()
        self.vectors_2D = None
        self.gt = dict()
        self.candidates = defaultdict(dict)
        self.embedding_cache = dict()
        self.vector_dump_file = None
        self.q_node_to_label = dict()
        self.metadata = []
        self.gt_indexes = set()
        self.input_format = ""
        self._logger = logging.getLogger(__name__)
        self.wikidata_server = "https://dsbox02.isi.edu:8888/bigdata/namespace/wdq/sparql"

    def get_sentences_embedding(self, sentences: typing.List[str]):
        """
            transform a list of sentences to embedding vectors
        """
        # if sentences in self.embedding_cache:
        #     return self.embedding_cache[sentences]
        # else:
        sentence_embeddings = self.model.encode(sentences, show_progress_bar=False)
            # self.embedding_cache[sentences] = sentence_embeddings
        return sentence_embeddings

    def send_sparql_query(self, query_body:str):
        from SPARQLWrapper import SPARQLWrapper, JSON, POST, URLENCODED
        qm = SPARQLWrapper(self.wikidata_server)
        qm.setReturnFormat(JSON)
        qm.setMethod(POST)
        qm.setRequestMethod(URLENCODED)
        self._logger.debug("Sent query is:")
        self._logger.debug(str(query_body))
        qm.setQuery(query_body)
        try:
            results = qm.query().convert()['results']['bindings']
            return results
        except:
            raise ValueError("Sending Sparl query to {} failed!".format(wikidata_server))

    def get_item_description(self, qnodes: typing.List[str]=None, target_properties:dict={}):
        """
            use sparql query to get the descriptions of given Q nodes
        """
        if qnodes is None:
            qnodes = self.candidates
        if "all" in target_properties:
            find_all_properties = True
        else:
            find_all_properties = False

        properties_list = [[] for _ in range(4)]
        names = ["labels", "descriptions", "isa_properties", "has_properties"]
        for k, v in target_properties.items():
            if v == "label_properties":
                properties_list[0].append(k)
            elif v == "description_properties":
                properties_list[1].append(k)
            elif v == "isa_properties":
                properties_list[2].append(k)
            elif v == "has_properties":
                properties_list[3].append(k)

        need_find_label = "label" in properties_list[0]
        need_find_description = "description" in properties_list[1]
        if need_find_label or need_find_description:
            query_qnodes = ""
            # descriptions = {}
            for each in qnodes:
                query_qnodes += "wd:{} ".format(each)
            query_body = """
                select ?item ?itemDescription ?itemLabel
                where {
                  values ?item {""" + query_qnodes + """ }
                     SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
                }
            """
            results = self.send_sparql_query(query_body)
            for each in results:
                name = each['item']['value'].split("/")[-1]
                if 'itemDescription' in each:
                    # clean up the descriptions
                    # description = " ".join(token_patern.findall(each['itemDescription']['value']))
                    description = each['itemDescription']['value']
                else:
                    description = ""
                if "itemLabel" in each:
                    label = each['itemLabel']['value']
                else:
                    label = ""
                if need_find_label:
                    self.candidates[name]["label_properties"] = [label]
                if need_find_description:
                    self.candidates[name]["description_properties"] = [description]


        query_body2 = "select ?item"
        part2 = ""
        for name, part in zip(names, properties_list):
            for i, each in enumerate(part):
                query_body2 += " ?{}_{}Label".format(name, i)
                part2 += """?item wdt:{} ?{}_{}. \n""".format(each, name, i)
        query_body2 += """
                    where {
                      values ?item {""" + query_qnodes + "}" 

        query_body2 += part2 + """
                         SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
                    }
        """
        results2 = self.send_sparql_query(query_body2)
        
        for each in results2:
            node_name = each['item']['value'].split("/")[-1]
            for name, part in zip(names, properties_list):
                if len(part) > 0:
                    properties_res = set()
                    for i in range(len(part)):
                        property_key = '{}_{}Label'.format(name, i)
                        if property_key in each:
                            properties_res.add(each[property_key]['value'])
                    self.candidates[node_name][name] = properties_res

        # if find_all_properties:

        # import pdb
        # pdb.set_trace()
        # self.candidates[name]["isa_properties"] = 
        # return descriptions

    def read_input(self, file_path: str, skip_nodes_set: set=None, 
                   input_format: str="kgtk_format",target_properties: dict={},
                   property_labels_dict:dict={}, black_list_set:set=set()
                   ):
        """
            load the input candidates files
        """
        if "all" in target_properties:
            _ = target_properties.pop("all")
            add_all_properties = True
        else:
            add_all_properties = False

        from collections import defaultdict
        import pandas as pd
        if input_format == "test_format":
            self.input_format = input_format
            input_df = pd.read_csv(file_path)
            candidates = {}
            gt = {}
            count = 0
            if "GT_kg_id" in input_df.columns:
                gt_column_id = "GT_kg_id"
            elif "kg_id" in input_df.columns:
                gt_column_id = "kg_id"
            else:
                raise ValueError("Can't find ground truth id column!")

            for _, each in input_df.iterrows():
                temp = each['candidates'].split("|")
                to_remove_q = set()
                gt_nodes = each[gt_column_id].split(" ")
                # candidates[each['label']] = temp
                temp.extend(gt_nodes)
                for each_q in temp:
                    self.q_node_to_label[each_q] = each['label']
                    if skip_nodes_set is not None and each_q in skip_nodes_set:
                        to_remove_q.add(each_q)
                temp = set(temp) - to_remove_q
                count += len(temp)
                gt[each['label']] = each[gt_column_id]
                self.get_item_description(temp, target_properties)
            # self.candidates = candidates
            self.gt = gt
            
            self._logger.info("Totally {} rows with {} candidates loaded.".format(str(len(gt)), str(count)))

        elif input_format == "kgtk_format":
            # assume the input edge file is sorted
            headers = None
            candidates_properties = defaultdict(dict)
            self.input_format = input_format
            with open(file_path, "r") as f:
                for each_line in f.readlines():
                    each_line = each_line.replace("\n", "").split("\t")
                    if headers is None:
                        headers = each_line
                        if len(headers) < 3:
                            raise ValueError("No enough columns found on given input file. Only {} columns given but at least 3 needed.".format(len(headers)))
                        elif "node" in headers and "property" in headers and "value" in headers:
                            column_references = {"node": headers.index("node"), 
                                                 "property": headers.index("property"),
                                                 "value": headers.index("value")}
                        elif len(headers) == 3:
                            column_references = {"node": 0, 
                                                 "property": 1,
                                                 "value": 2}
                        else:
                            missing_column = set(["node", "property", "value"]) - set(headers)
                            raise ValueError("Missing column {}".format(missing_column))

                        self._logger.debug("column index information: ")
                        self._logger.debug(str(column_references))
                    else:
                        node_id = each_line[column_references["node"]]
                        node_property = each_line[column_references["property"]]
                        node_value = each_line[column_references["value"]]
                        candidates_properties[node_id][node_property] = node_value
                        # remove @ mark
                        if "@" in node_value and node_value[0] != "@":
                            node_value_org = node_value
                            node_value = node_value[:node_value.index("@")]
                            # print("{} --> {}".format(node_value_org, node_value))
                        # remove extra double quote "
                        if node_value[0]== '"' and node_value[-1] == '"':
                            node_value = node_value[1:-1]
                        
                        if node_property in target_properties:
                            # only use the fisrt value get (except has-properties part)
                            # if target_properties[node_property] in candidates_properties[node_id].keys() or \
                            # target_properties[node_property] == "has_properties":
                            if target_properties[node_property] != "has_properties":
                                if target_properties[node_property] in candidates_properties[node_id]:
                                    candidates_properties[node_id][target_properties[node_property]].append(node_value)
                                else:
                                    candidates_properties[node_id][target_properties[node_property]] = [node_value]
                            else:
                                if node_value not in black_list_set:
                                    if target_properties[node_property] in candidates_properties[node_id]:
                                        candidates_properties[node_id][target_properties[node_property]].append(node_property)
                                    else:
                                        candidates_properties[node_id][target_properties[node_property]] = [node_property]
                        elif add_all_properties:
                            if node_value not in black_list_set:
                                if "has_properties" in candidates_properties[node_id]:
                                    candidates_properties[node_id]["has_properties"].append(node_property)
                                else:
                                    candidates_properties[node_id]["has_properties"] = [node_property]

            self.candidates = candidates_properties
        else:
            raise ValueError("Unkonwn input format {}".format(input_format))

        # concate all properties into one sentence to represent the Q node
        for k, v in self.candidates.items():
            concated_sentence = ""
            if "label_properties" in v:
                concated_sentence += self.get_real_label_name(v["label_properties"][0], property_labels_dict)
            if "description_properties" in v:
                if concated_sentence != "" and v["description_properties"][0] != "":
                    concated_sentence += ", "
                concated_sentence += self.get_real_label_name(v["description_properties"][0], property_labels_dict)
            if "isa_properties" in v:
                temp = [self.get_real_label_name(each, property_labels_dict) for each in v["isa_properties"]]
                if concated_sentence != "" and temp[0] != "":
                    concated_sentence += " is a " 
                elif temp[0] != "":
                    concated_sentence += "It is a "
                concated_sentence += ", ".join(temp)
            if "has_properties" in v:
                temp = [self.get_real_label_name(each, property_labels_dict) for each in v["has_properties"]]
                if concated_sentence != "" and temp[0] != "":
                    concated_sentence += ", and has "
                elif temp[0] != "":
                    concated_sentence += "It has "
                concated_sentence += " and ".join(temp)
            self.candidates[k]["sentence"] = concated_sentence
            self._logger.debug("{} --> {}".format(k, concated_sentence))
            self._logger.info("Totally {} Q nodes loaded.".format(len(self.candidates)))


        self.vector_dump_file = "dump_vectors_{}_{}.pkl".format(file_path[:file_path.rfind(".")], self. model_name)
        # self._logger.debug("The cache file name will be {}".format(self.vector_dump_file))

    def get_real_label_name(self, node, property_labels_dict):
        if node in property_labels_dict:
            return property_labels_dict[node]
        else:
            return node

    def get_vetors(self, use_cache=True, vector_dump_file=None):
        """
            main function to get the vector representations of the descriptions
        """
        import os
        import time
        from tqdm import tqdm
        if vector_dump_file is None:
            vector_dump_file = self.vector_dump_file
        if use_cache and os.path.exists(vector_dump_file):
            self._logger.info("Using cached vector file!")
            self.load_vectors(vector_dump_file)
            return
            
        start_all = time.time()
        jobs_count = 0
        counter = 0
        self._logger.info("Now generating embedding vector.")
        # embed_time, query_time, rem_time = 0, 0, 0
        # if self.input_format == "test_format":
        #     for each in tqdm(self.candidates.values()):
        #         # do process for each row(one target)
        #         start = time.time()
        #         each_part_description = self.get_item_description(each)
        #         end1 = time.time()
        #         query_time += end1 - start
        #         vectors = self.get_sentences_embedding(list(each_part_description.values()))
        #         end2 = time.time()
        #         embed_time += end2 - end1
        #         for q_node, vector in zip(list(each_part_description.keys()), vectors):
        #             self.vectors_map[q_node] = vector
        #     self._logger.debug("query time = {} s, embedding time = {} s".format(query_time, embed_time))
        #     # self.dump_vectors(vector_dump_file)

        # elif self.input_format == "kgtk_format":
        for q_node, each_item in tqdm(self.candidates.items()):
            # do process for each row(one target)
            sentence = each_item["sentence"]
            vectors = self.get_sentences_embedding([sentence])
            self.vectors_map[q_node] = vectors[0]
        self._logger.info("Totally used {} seconds.".format(str(time.time() - start_all)))

    def dump_vectors(self, file_name, type_=None):
        if file_name.endswith(".pkl"):
            file_name = file_name.replace(".pkl", "")
        if type_ == "2D":
            with open(file_name + ".pkl", "wb") as f:
                pickle.dump(self.vectors_2D, f)
            dimension = len(self.vectors_2D[0])
            with open(file_name + ".tsv", "w") as f:
                for each in self.vectors_2D:
                    for i, each_val in enumerate(each):
                        _ = f.write(str(each_val))
                        if i != dimension - 1:
                            _ = f.write("\t")
                    _ = f.write("\n")
        elif type_ == "metadata":
            with open(file_name + "_metadata.tsv", "w") as f:
                for each in self.metadata:
                    _ = f.write(each + "\n")
        else:
            with open(file_name + ".pkl", "wb") as f:
                pickle.dump(self.vectors_map, f)
            with open(file_name + ".tsv", "w") as f:
                for each in self.vectors_map.values():
                    for i in each:
                        _ = f.write(str(i) + "\t")
                    _ = f.write("\n")

    def load_vectors(self, file_name, type_=None):
        if type_ == "2D":
            with open(file_name, "rb") as f:
                self.vectors_2D = pickle.load(f)
        else:
            with open(file_name, "rb") as f:
                self.vectors_map = pickle.load(f)
    
    def print_vector(self, vectors, output_properties:str="text_embedding", output_format="kgtk_format"):
        if output_format == "kgtk_format":
            print("node\tproperty\tvalue\n", end="")
            if self.input_format == "kgtk_format":
                for i, each_vector in enumerate(vectors):
                    print(str(list(self.candidates.keys())[i]) + "\t", end="")
                    print(output_properties + "\t", end="")
                    for j, each_dimension in enumerate(each_vector):
                        if j != len(each_vector) - 1:
                            print(str(each_dimension) + ",", end="")
                        else:
                            print(str(each_dimension) + "\n", end="")
            elif self.input_format == "test_format":
                all_nodes = list(self.vectors_map.keys())
                for i, each_vector in enumerate(vectors):
                    print(all_nodes[i] + "\t", end="")
                    print(output_properties + "\t", end="")
                    for j, each_dimension in enumerate(each_vector):
                        if j != len(each_vector) - 1:
                            print(str(each_dimension) + ",", end="")
                        else:
                            print(str(each_dimension) + "\n", end="")

        elif output_format == "tsv_format":
            for each_vector in vectors:
                for i, each_dimension in enumerate(each_vector):
                    if i != len(each_vector) - 1:
                        print(str(each_dimension) + "\t", end="")
                    else:
                        print(str(each_dimension) + "\n", end="")


    def plot_result(self, use_cache=True, vector_dump_file=None, 
                    output_properties={}, input_format="kgtk_format", 
                    output_uri:str="", output_format="kgtk_format",
                    run_TSNE=True
                    ):
        """
            transfer the vectors to lower dimension so that we can plot
            Then save the 2D vector file for further purpose
        """
        import os
        import time
        from sklearn.manifold import TSNE

        # if vector_dump_file is None:
        #     vector_dump_file = self.vector_dump_file.replace(".pkl", "_2D.pkl")
        # if use_cache and os.path.exists(vector_dump_file):
        #     self._logger.info("Using cached 2D vector file!")
        #     self.load_vectors(vector_dump_file, "2D")
        # else:
        vectors = list(self.vectors_map.values())
        # use tsne to reduce dimension
        if run_TSNE:
            self._logger.warning("Start running TSNE to reduce dimension. It will take a long time.")
            start = time.time()
            self.vectors_2D = TSNE(n_components=2).fit_transform(vectors)
            # self.dump_vectors(vector_dump_file, "2D")
            self._logger.info("Totally used {} seconds.".format(time.time() - start))

        if input_format == "test_format":
            # # start plot
            gt_indexes = set()
            for each in self.gt.values():
                all_nodes = each.split(" ")
                for each_node in all_nodes:
                    gt_indexes.add(list(self.vectors_map.keys()).index(each_node))
            # load the descriptions if we don't have them
            # if len(self.q_nodes_descriptions) == 0:
            #     for each in self.candidates.values():
            #         _ = self.get_item_description(each)

            self.metadata.append("Q_nodes\tType\tLabel\tDescription")
            for i, each in enumerate(self.vectors_map.keys()):
                label = self.q_node_to_label[each]
                description = self.candidates[each]["sentence"]
                if i in gt_indexes:
                    self.metadata.append("{}\tground_truth_node\t{}\t{}".format(each, label, description))
                else:
                    self.metadata.append("{}\tcandidates\t{}\t{}".format(each, label, description))
            self.gt_indexes = gt_indexes

        elif input_format == "kgtk_format":
            if len(output_properties.get("metatada_properties", [])) == 0:
                for k, v in self.candidates.items():
                    label = v.get("label_properties", "")
                    if len(label) > 0 and isinstance(label, list):
                        label = label[0]
                    description = v.get("description_properties", "")
                    if len(description) > 0 and isinstance(description, list):
                        description = description[0]
                    self.metadata.append("{}\t\t{}\t{}".format(k, label, description))
            else:
                required_properties = output_properties["metatada_properties"]
                self.metadata.append("node\t" + "\t".join(required_properties))
                for k, v in self.candidates.items():
                    each_metadata = k + "\t"
                    for each in required_properties:
                        each_metadata += v.get(each, " ") + "\t"
                    self.metadata.append(each_metadata)

        metadata_output_path = os.path.join(output_uri, self.vector_dump_file.split("/")[-1])
        if run_TSNE:
            self.print_vector(self.vectors_2D, output_properties.get("output_properties"), output_format)
        else:
            self.print_vector(vectors, output_properties.get("output_properties"), output_format)
        self.dump_vectors(metadata_output_path, "metadata")

    def evaluate_result(self):
        """
            for the ground truth nodes, evaluate the average distance to the centroid, the lower the average distance, the better clustering results should be
        """
        centroid = None
        gt_nodes_vectors = []
        if len(self.gt_indexes) == 0:
            points = set(range(len(self.vectors_map)))
        else:
            points = self.gt_indexes
        for i, each in enumerate(self.vectors_map.keys()):
            # label = self.q_node_to_label[each]
            # description = self.q_nodes_descriptions.get(each, "")
            if i in points:
                if centroid is None:
                    centroid = self.vectors_map[each]
                else:
                    centroid += self.vectors_map[each]
                gt_nodes_vectors.append(self.vectors_map[each])
        centroid = centroid / len(points)
        
        distance_sum = 0
        for each in gt_nodes_vectors:
            distance_sum += self.calculate_distance(each, centroid)
        self._logger.warning("The average distance for the ground truth nodes to centroid is {}".format(distance_sum / len(points)))

    @staticmethod
    def calculate_distance(a, b):
        if len(a) != len(b):
            raise ValueError("Vector dimension are different!")
        dist = 0
        for v1, v2 in zip(a,b):
            dist += (v1 - v2) **2
        dist = dist ** 0.5
        return dist

def load_embedding_model_names():
    names = []
    import os
    model_file_path = os.path.join(repr(__file__).replace("'","").replace("/text_embedding.py", ""), "all_embedding_models_names.txt")
    if os.path.exists(model_file_path):
        with open(model_file_path, "r") as f:
            for each_line in f.readlines():
                names.append(each_line.replace("\n", ""))
    else:
        raise ValueError("Embedding model names list file lost! Please check.")
    return names

def load_property_labels_file(input_files: typing.List[str]):
    labels_dict = {}
    headers = None
    for each_file in input_files:
        with open(each_file, "r") as f:
            for each_line in f.readlines():
                each_line = each_line.replace("\n", "").split("\t")
                if headers is None:
                    headers = each_line
                    if len(headers) < 2:
                        raise ValueError("No enough columns found on given input file. Only {} columns given but at least 2 needed.".format(len(headers)))
                    elif "predicate" in headers and "label" in headers:
                        column_references = {"predicate": headers.index("predicate"), 
                                             "label": headers.index("label")}
                    elif "label" in headers:
                        column_references = {"predicate": 0, 
                                             "label": headers.index("label"),
                                             }
                    else:
                        raise ValueError("Can't determine which column is label column for label file!")

                else:
                    node_id = each_line[column_references["predicate"]]
                    node_label = each_line[column_references["label"]]
                    if "@en" in node_label:
                        node_label = node_label.replace("'", "").replace("@en", "")
                        labels_dict[node_id] = node_label
                    if node_id not in labels_dict:
                        labels_dict[node_id] = node_label
    return labels_dict


def load_black_list_files(file_path):
    import tarfile
    import zipfile
    import gzip
    token_patern = re.compile(r"(?u)\b\w\w+\b")
    q_nodes_set = set()
    for each_file in file_path:
        try:
            # tar.gz file
            if each_file.endswith("tar.gz"):
                tar = tarfile.open("filename.tar.gz", "r:gz")
                for member in tar.getmembers():
                     f = tar.extractfile(member)
                     if f:
                         content = f.read()
                         Data = np.loadtxt(content)
            # gz file
            elif each_file.endswith(".gz"):
                with gzip.open('big_file.txt.gz', 'rb') as f:
                    input_data = f.readlines()
            # zip file
            elif each_file.endswith(".zip"):
                archive = zipfile.ZipFile(each_file, 'r')
                input_data = archive.read(each_file.replace(".zip", "")).decode().split("\n")
            # other file, just read directly
            else:
                with open(each_file, "r") as f:
                    input_data = f.readlines()

            
            for each in input_data:
                each = each.replace("\n", "")
                for each_part in token_patern.findall(each):
                    if each_part[0] == "Q" and each_part[1:].isnumeric():
                        q_nodes_set.add(each_part)
        except Exception as e:
            _logger.error("Load black list file {} failed!".format(each_file))
            _logger.debug(e, exc_info=True)

    _logger.info("Totally {} black list nodes loadded.".format(len(q_nodes_set)))
    return q_nodes_set


def main(**kwargs):
    # setup logger format
    # console = logging.StreamHandler()
    # console.setLevel(logging.DEBUG)
    # formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(lineno)d -- %(message)s", '%m-%d %H:%M:%S')
    # console.setFormatter(formatter)
    # logging.getLogger('').addHandler(console)

    import logging
    import os
    import time
    from time import strftime
    logging_level = kwargs.get("logging_level", "warning")
    if logging_level == "info":
        logging_level_class = logging.INFO
    elif logging_level == "debug":
        logging_level_class = logging.DEBUG
    elif logging_level == "warning":
        logging_level_class = logging.WARNING
    elif logging_level == "error":
        logging_level_class = logging.ERROR
    else:
        logging_level_class = logging.WARNING
    logger_path = os.path.join(os.environ.get("HOME"), "kgtk_text_embedding_log_{}.log".format(strftime("%Y-%m-%d-%H-%M")))
    logging.basicConfig(level=logging_level_class,
                format="%(asctime)s [%(levelname)s] %(name)s %(lineno)d -- %(message)s",
                datefmt='%m-%d %H:%M:%S',
                filename=logger_path,
                filemode='w')
    _logger = logging.getLogger(__name__)
    _logger.warning("Running with logging level {}".format(_logger.getEffectiveLevel()))
    import torch
    import typing

    import pandas as pd
    import string
    import math
    import re
    import argparse
    import pickle

    # get input parameters from kwargs
    output_uri = kwargs.get("output_uri", "")
    black_list_files = kwargs.get("black_list_files", "")
    all_models_names = kwargs.get("all_models_names", ['bert-base-wikipedia-sections-mean-tokens'])
    input_format = kwargs.get("input_format", "kgtk_format")
    input_uris = kwargs.get("input_uris", [])
    output_format = kwargs.get("output_format", "kgtk_format")
    property_labels_files = kwargs.get("property_labels_file_uri", "")
    properties = dict()
    all_property_relate_inputs = [kwargs.get("label_properties", ["label"]), 
                                  kwargs.get("description_properties", ["description"]),
                                  kwargs.get("isa_properties", ["P31"]),
                                  kwargs.get("has_properties", ["all"]),
                                 ]
    all_required_properties = ["label_properties", "description_properties", 
                               "isa_properties", "has_properties"]

    for each_property, each_input in zip(all_required_properties, all_property_relate_inputs):
        for each in each_input:
            properties[each] = each_property

    
    output_properties = {
        "metatada_properties": kwargs.get("metatada_properties", []),
        "output_properties": kwargs.get("output_properties", "text_embedding")
    }

    if isinstance(all_models_names, str):
        all_models_names = [all_models_names]
    if isinstance(input_uris, str):
        input_uris = [input_uris]
    if len(all_models_names) == 0:
        raise ValueError("No embedding vector model name given!")
    if len(input_uris) == 0:
        raise ValueError("No input file path given!")

    if output_uri == "":
        output_uri = os.getenv("HOME") # os.getcwd()
    if black_list_files != "":
        black_list_set = load_black_list_files(black_list_files)
    else:
        black_list_set = set()
    if property_labels_files:
        property_labels_dict = load_property_labels_file(property_labels_files)
    else:
        property_labels_dict = {}
        _logger.info("Totally {} property labels loaded.".format(len(property_labels_dict)))
    run_TSNE = kwargs.get("run_TSNE", True)

    try:
        for each_model_name in all_models_names:
            for each_input_file in input_uris:
                _logger.info("Running {} model on {}".format(each_model_name, each_input_file))
                process = EmbeddingVector(each_model_name)
                process.read_input(file_path=each_input_file, skip_nodes_set=black_list_set, 
                                   input_format=input_format, target_properties=properties,
                                   property_labels_dict=property_labels_dict)
                process.get_vetors(use_cache=True)
                process.plot_result(use_cache=True, output_properties=output_properties, 
                                    input_format=input_format, output_uri=output_uri, 
                                    run_TSNE=run_TSNE, output_format=output_format)
                process.evaluate_result()
                _logger.info("*" * 20 + "finished" + "*" * 20)
    except Exception as e:
        _logger.debug(e, exc_info=True)


def parser():
    return {
        'help': """Produce embedding vectors on given file's nodes."""
    }

def add_arguments(parser):
    import argparse
    def str2bool(v):
        if isinstance(v, bool):
           return v
        if v.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        elif v.lower() in ('no', 'false', 'f', 'n', '0'):
            return False
        else:
            raise argparse.ArgumentTypeError('Boolean value expected.')
    # logging level
    parser.add_argument('-l', '--logging-level', action='store', dest='logging_level',
            default="info", choices=("warning, info, debug"),
            help="set up the logging level, default is INFO level")
    # model name
    all_models_names = load_embedding_model_names()
    parser.add_argument('-m', '--model', action='store', nargs='+', dest='all_models_names',
            default="bert-base-wikipedia-sections-mean-tokens", choices=all_models_names,
            help="the model to used for embedding")
    # input file
    parser.add_argument('-i', '--input', action='store', nargs='+', dest='input_uris',
            help="input path",)
    parser.add_argument('-f', '--input-format', action='store', dest='input_format', 
            choices=("test_format", "kgtk_format"), default = "kgtk_format",
            help="the input file format, could either be `test_format` or `kgtk_format`, default is `kgtk_format`",)
    parser.add_argument('-p', '--property-labels-file', action='store', nargs='+', 
            dest='property_labels_file_uri', help="the path to the property labels file.",)
    # properties (only valid for kgtk format input/output data)
    parser.add_argument('--label-properties', action='store', nargs='+', 
            dest='label_properties',default= ["label"],
            help="""The names of the eges for label properties, Default is ["label"]. \n This argument is only valid for input in kgtk format.""")
    parser.add_argument('--description-properties', action='store', nargs='+', 
            dest='description_properties', default= ["description"],
            help="""The names of the eges for description properties, Default is ["description"].\n This argument is only valid for input in kgtk format.""")
    parser.add_argument('--isa-properties', action='store', nargs='+', 
            dest='isa_properties', default= ["P31"],
            help="""The names of the eges for `isa` properties, Default is ["P31"] (the `instance of` node in wikidata).\n This argument is only valid for input in kgtk format.""")
    parser.add_argument('--has-properties', action='store', nargs='+', 
            dest='has_properties', default= ["all"],
            help="""The names of the eges for `has` properties, Default is ["all"] (will automatically append all properties found for each node).\n This argument is only valid for input in kgtk format.""")
    parser.add_argument('--output-property', action='store', 
            dest='output_properties', default= "text_embedding",
            help="""The output property name used to record the embedding. Default is `output_properties`. \nThis argument is only valid for output in kgtk format.""")
    # output
    parser.add_argument('-o', '--embedding-projector-metadata-path', action='store', dest='output_uri', default="",
            help="output path for the metadata file, default will be current user's home directory")
    parser.add_argument('--output-format', action='store', dest='output_format', 
            default="kgtk", choices=("tsv_format", "kgtk_format"),
            help="output format, can either be `tsv_format` or `kgtk_format`. \nIf choose `tsv_format`, the output will be a tsv file, with each row contains only the vector representation of a node. Each dimension is separated by a tab")
    parser.add_argument('--embedding-projector-metatada', action='store', nargs='+', 
            dest='metatada_properties', default= [],
            help="""list of properties used to construct a metadata file for use in the Google Embedding Projector: http://projector.tensorflow.org. \n Default: the label and description of each node.""")
    # black list file
    parser.add_argument('-b', '--black-list', nargs='+', action='store', dest='black_list_files',
            default= "",
            help="the black list file, contains the Q nodes which should not consider as candidates.")
    # run tsne or not
    parser.add_argument("--run-TSNE", type=str2bool, nargs='?',  action='store',
                        default=True, dest="run_TSNE",
                        help="whether to run TSNE or not after the embedding, default is true.")


def run(**kwargs):
    main(**kwargs)
