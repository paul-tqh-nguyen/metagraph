import pytest

grblas = pytest.importorskip("grblas")

import metagraph as mg
from metagraph.core.resolver import Resolver
from metagraph.dask import DaskResolver
from metagraph.core.dask.placeholder import Placeholder
from metagraph.tests.util import default_plugin_resolver, example_resolver
from metagraph.plugins.python.types import PythonNodeMapType
from metagraph.plugins.numpy.types import NumpyNodeMap
from metagraph.plugins.graphblas.types import GrblasNodeMap
from metagraph.plugins.networkx.types import NetworkXGraph
from metagraph.plugins.scipy.types import ScipyGraph
from metagraph.plugins.scipy.algorithms import ss_graph_filter_edges
from metagraph.plugins.networkx.algorithms import nx_graph_aggregate_edges
import networkx as nx
import dask


def test_dask_resolver(example_resolver):
    res = example_resolver
    dres = DaskResolver(res)
    assert set(dir(dres)) == set(dir(res)) | {"delayed_wrapper"}

    with pytest.raises(
        NotImplementedError,
        match="Register with the resolver prior to creating a DaskResolver",
    ):
        dres.register()


def test_delayed_wrapper(default_plugin_resolver):
    dpr = default_plugin_resolver
    if not isinstance(dpr, DaskResolver):
        dpr = DaskResolver(dpr)

    dvec = dpr.delayed_wrapper(
        grblas.Vector.from_values, dpr.types.Vector.GrblasVectorType
    )
    my_vec = dvec([0, 1, 2], [2.2, 3.3, 9.9])
    assert isinstance(my_vec, Placeholder)

    with pytest.raises(
        TypeError, match="is not a defined `value_type`. Must provide `concrete_type`"
    ):
        dpr.delayed_wrapper(grblas.Vector.from_values)


def test_translation_direct(default_plugin_resolver):
    dpr = default_plugin_resolver
    if not isinstance(dpr, DaskResolver):
        dpr = DaskResolver(dpr)
    x = {0: 12.5, 1: 33.4, 42: -1.2}
    final = GrblasNodeMap(
        grblas.Vector.from_values([0, 1, 42], [12.5, 33.4, -1.2], size=43),
    )
    y = dpr.translate(x, NumpyNodeMap)
    z = dpr.translate(y, GrblasNodeMap)
    assert isinstance(y, Placeholder)
    assert isinstance(z, Placeholder)
    assert len(y._dsk.keys()) == 1  # Only one task to perform
    assert len(z._dsk.keys()) == 2  # Two tasks to perform because y is still lazy
    dpr.assert_equal(z, final)


def test_translation_multistep(default_plugin_resolver):
    dpr = default_plugin_resolver
    res_small = Resolver()
    # Only register some of the translators to force a multi-step translation path
    res_small.register(
        {
            "foo": {
                "abstract_types": dpr.abstract_types,
                "concrete_types": dpr.concrete_types,
                "wrappers": {PythonNodeMapType, NumpyNodeMap, GrblasNodeMap},
                "translators": {
                    dpr.translators[(PythonNodeMapType, NumpyNodeMap.Type)],
                    dpr.translators[(NumpyNodeMap.Type, GrblasNodeMap.Type)],
                },
            }
        }
    )
    ldpr = DaskResolver(res_small)
    x = {0: 12.5, 1: 33.4, 42: -1.2}
    final = GrblasNodeMap(
        grblas.Vector.from_values([0, 1, 42], [12.5, 33.4, -1.2], size=43),
    )
    z = ldpr.translate(x, GrblasNodeMap)
    assert isinstance(z, Placeholder)
    assert len(z._dsk.keys()) == 2  # Only one translation, but creates two tasks
    ldpr.assert_equal(z, final)


def test_algo_chain(default_plugin_resolver):
    dpr = default_plugin_resolver
    res_small = Resolver()
    # Only register some of the translators to force a multi-step translation path
    res_small.register(
        {
            "foo": {
                "abstract_types": dpr.abstract_types,
                "concrete_types": dpr.concrete_types,
                "wrappers": {
                    PythonNodeMapType,
                    NumpyNodeMap,
                    GrblasNodeMap,
                    NetworkXGraph,
                },
                "translators": {
                    dpr.translators[(PythonNodeMapType, NumpyNodeMap.Type)],
                    dpr.translators[(NumpyNodeMap.Type, GrblasNodeMap.Type)],
                    dpr.translators[(NetworkXGraph.Type, ScipyGraph.Type)],
                    dpr.translators[(ScipyGraph.Type, NetworkXGraph.Type)],
                },
                "abstract_algorithms": set(dpr.abstract_algorithms.values()),
                "concrete_algorithms": {
                    nx_graph_aggregate_edges,
                    ss_graph_filter_edges,
                },
            }
        }
    )
    ldpr = DaskResolver(res_small)
    g = nx.Graph()
    g.add_weighted_edges_from(
        [(0, 1, 1), (0, 2, 2), (0, 3, 3), (0, 4, 4), (2, 4, 5), (3, 4, 6)]
    )
    graph = ldpr.wrappers.Graph.NetworkXGraph(g)  # this is a Placeholder
    assert (
        repr(ldpr.wrappers.Graph.NetworkXGraph) == "DelayedWrapper<NetworkXGraphType>"
    )
    assert isinstance(graph, Placeholder)
    sum_of_all_edges = {0: 10, 1: 1, 2: 7, 3: 9, 4: 15}
    sum_of_filtered_edges = {0: 7, 1: 0, 2: 5, 3: 9, 4: 15}
    # Verify simple algorithm call works (no translations required)
    nm = ldpr.algos.util.graph.aggregate_edges(
        graph, lambda x, y: x + y, initial_value=0
    )
    assert isinstance(nm, Placeholder)
    assert nm.concrete_type is PythonNodeMapType
    assert len(nm._dsk.keys()) == 2  # init, aggregate
    ldpr.assert_equal(nm, sum_of_all_edges)
    # Build chained algo call with translators
    graph2 = ldpr.algos.util.graph.filter_edges(graph, func=lambda x: x > 2)
    assert isinstance(graph2, Placeholder)
    assert graph2.concrete_type is ScipyGraph.Type
    assert len(graph2._dsk.keys()) == 3  # init, translate, filter
    nm2 = ldpr.algos.util.graph.aggregate_edges(
        graph2, lambda x, y: x + y, initial_value=0
    )
    assert isinstance(nm2, Placeholder)
    assert nm2.concrete_type is PythonNodeMapType
    assert len(nm2._dsk.keys()) == 5  # init, translate, filter, translate, aggregate
    ldpr.assert_equal(nm2, sum_of_filtered_edges)


def test_call_using_dispatcher(default_plugin_resolver):
    dpr = default_plugin_resolver
    if not isinstance(dpr, DaskResolver):
        dpr = DaskResolver(dpr)
    pnm = {0: 1, 1: 2}
    result = dpr.algos.util.nodemap.reduce(pnm, lambda x, y: x + y)
    assert result.compute() == 3


def test_call_errors(example_resolver):
    dres = DaskResolver(example_resolver)
    with pytest.raises(
        TypeError,
        match='No concrete algorithm for "ln" can be satisfied for the given inputs',
    ):
        dres.algos.ln(14)


def test_include_resolver(example_resolver):
    dres = DaskResolver(example_resolver)

    # Dig deep to grab the underlying translator and concrete algorithm
    # rather than relying on the resolver to do that for us.
    # This allows us to exercise which resolver is passed in to the calls.

    # Find translator
    src = example_resolver.wrappers.MyNumericAbstractType.StrNum("13")
    src_type = example_resolver.typeclass_of(src)
    dst_type = example_resolver.types.MyNumericAbstractType.StrNumRot13Type
    mst = mg.core.planning.MultiStepTranslator.find_translation(
        example_resolver, src_type, dst_type, exact=True
    )
    translator = mst.translators[0]
    num13 = translator(src, resolver=dres)

    # Find concrete algorithm
    algorithm = dres.find_algorithm_exact("power", num13, num13)
    result13 = algorithm(num13, num13, resolver=dres)
    assert result13.compute().value == "302875106592253"


def test_call_using_exact_dispatcher(default_plugin_resolver):
    dpr = default_plugin_resolver
    if not isinstance(dpr, DaskResolver):
        dpr = DaskResolver(dpr)
    g = nx.Graph()
    g.add_weighted_edges_from([(0, 1, 12), (1, 2, 5), (2, 0, 8)])
    nxg = dpr.wrappers.Graph.NetworkXGraph(g)
    result = dpr.algos.centrality.pagerank.core_networkx(nxg)
    assert isinstance(result, Placeholder)

    # Exercise the persist functionality
    result = result.persist()
    assert isinstance(result, Placeholder)

    # Check the result
    assert result.compute() == {0: 1 / 3, 1: 1 / 3, 2: 1 / 3}


def test_call_exact_errors(example_resolver):
    dres = DaskResolver(example_resolver)
    with pytest.raises(
        TypeError,
        match="Incorrect input types and no valid translation path to solution",
    ):
        dres.algos.ln.example_plugin(14)
    with pytest.raises(
        TypeError, match="Incorrect input types. Translations required for"
    ):
        dres.algos.power.example2_plugin(14, 2)
