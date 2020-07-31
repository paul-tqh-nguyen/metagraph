from metagraph import translator
from metagraph.plugins import has_scipy, has_networkx, has_grblas
from metagraph.plugins.numpy.types import NumpyNodeMap
from metagraph.plugins.python.types import PythonNodeSet
import numpy as np

if has_scipy:
    import scipy.sparse as ss
    from .types import ScipyEdgeMap, ScipyEdgeSet, ScipyMatrixType
    from ..numpy.types import NumpyMatrix

    @translator
    def edgemap_to_edgeset(x: ScipyEdgeMap, **props) -> ScipyEdgeSet:
        data = x.value.copy()
        # Force all values to be 1's to indicate no weights
        data.data = np.ones_like(data.data)
        return ScipyEdgeSet(data, x.node_list, x.transposed)

    @translator
    def matrix_from_numpy(x: NumpyMatrix, **props) -> ScipyMatrixType:
        # scipy.sparse assumes zero mean empty
        # To work around this limitation, we use a mask
        # and directly set .data after construction
        if x.mask is None:
            mat = ss.coo_matrix(x)
            nrows, ncols = mat.shape
            if mat.nnz != nrows * ncols:
                mat.data = x.value.flatten()
        else:
            mat = ss.coo_matrix(x.mask)
            mat.data = x.value[x.mask]
        return mat


if has_scipy and has_networkx:
    import networkx as nx
    from .types import ScipyGraph
    from ..networkx.types import NetworkXGraph

    @translator
    def graph_from_networkx(x: NetworkXGraph, **props) -> ScipyGraph:
        aprops = NetworkXGraph.Type.compute_abstract_properties(
            x, {"node_type", "edge_type"}
        )
        ordered_nodes = list(sorted(x.value.nodes()))
        is_sequential = ordered_nodes[-1] == len(ordered_nodes) - 1
        if aprops["node_type"] == "map":
            node_vals = np.array(
                [x.value.nodes[n].get(x.node_weight_label) for n in ordered_nodes]
            )
            if is_sequential:
                nodes = NumpyNodeMap(node_vals)
            else:
                nodes = NumpyNodeMap(node_vals, node_ids=np.array(ordered_nodes))
        elif not is_sequential:
            nodes = PythonNodeSet(
                set(ordered_nodes)
            )  # TODO: change this to NumpyNodeSet
        else:
            nodes = None
        if aprops["edge_type"] == "map":
            m = nx.convert_matrix.to_scipy_sparse_matrix(
                x.value, nodelist=ordered_nodes, weight=x.edge_weight_label
            )
            edges = ScipyEdgeMap(m, ordered_nodes)
        else:
            m = nx.convert_matrix.to_scipy_sparse_matrix(
                x.value, nodelist=ordered_nodes
            )
            edges = ScipyEdgeSet(m, ordered_nodes)
        return ScipyGraph(edges, nodes)

    @translator
    def graph_to_networkx(x: ScipyGraph, **props) -> NetworkXGraph:
        is_directed = ScipyGraph.Type.compute_abstract_properties(x, {"is_directed"})[
            "is_directed"
        ]

        nx_graph = nx.from_scipy_sparse_matrix(
            x.edges.value,
            create_using=nx.DiGraph if is_directed else nx.Graph,
            edge_attribute="weight",
        )

        def _simplify_type(obj):
            simplified_obj = obj
            if np.issubdtype(type(obj), np.dtype(str).type):
                simplified_obj = str(simplified_obj)
            elif np.issubdtype(type(obj), np.dtype(int).type):
                simplified_obj = int(simplified_obj)
            elif np.issubdtype(type(obj), np.dtype(float).type):
                simplified_obj = float(simplified_obj)
            return simplified_obj

        for _, _, attr in nx_graph.edges(data=True):
            attr["weight"] = _simplify_type(attr["weight"])

        if x.edges.node_list is not None:
            pos2id = dict(enumerate(x.edges.node_list))
            nx.relabel_nodes(nx_graph, pos2id, False)

        if x.nodes is not None:
            if isinstance(x.nodes, PythonNodeSet):
                nx_graph.add_nodes_from(x.nodes.value)
            elif isinstance(x.nodes, NumpyNodeMap):
                # TODO consider making __iter__ a required method for NodeMap implementations or making __getitem__ handle sets of ids to simplify this sort of code
                make_weight_dict = lambda weight: {"weight": weight}
                if x.nodes.mask is not None:
                    ids = np.flatnonzero(x.nodes.mask)
                    attrs = map(make_weight_dict, x.nodes.value[x.nodes.mask])
                    id2attr = dict(zip(ids, attrs))
                elif x.nodes.id2pos is not None:
                    id2attr = {
                        node_id: make_weight_dict(x.nodes.value[pos])
                        for node_id, pos in x.nodes.id2pos.items()
                    }
                else:
                    id2attr = dict(enumerate(map(make_weight_dict, x.nodes.value)))
                nx.set_node_attributes(nx_graph, id2attr, name="weight")

        return NetworkXGraph(nx_graph)


if has_scipy and has_grblas:
    import scipy.sparse as ss
    from .types import ScipyMatrixType
    from ..graphblas.types import GrblasMatrixType

    @translator
    def matrix_from_graphblas(x: GrblasMatrixType, **props) -> ScipyMatrixType:
        rows, cols, vals = x.to_values()
        mat = ss.coo_matrix((vals, (rows, cols)), x.shape)
        return mat
