import numpy

import theano
from theano import tensor
from theano.tensor.subtensor import Subtensor, get_idx_list

from theano.gof.python25 import all, any

try:
    import pygpu
    from pygpu import gpuarray
except ImportError:
    pass

from type import GpuArrayType
from basic_ops import as_gpuarray_variable

class GpuSubtensor(Subtensor):
    def make_node(self, x, *inputs):
        rval = tensor.Subtensor.make_node(self, x, *inputs)
        otype = GpuArrayType(dtype=rval.outputs[0].type.dtype,
                             broadcastable=rval.outputs[0].type.broadcastable)
        x = as_gpuarray_variable(x)
        return Apply(self, [x] + rval.inputs[1:], [otype()])

    def perform(self, node, inputs, out_):
        out, = out_
        x = inputs[0]
        if self.perform_cache_cdata is not None:
            out[0] = x.__getitem__(self.perform_cache_cdata)
            return
        
        cdata = get_idx_list(inputs, self.idx_list)
        if len(cdata) == 1:
            cdata = cdata[0]
        if len(inputs) == 1:
            self.perform_cache_cdata = cdata
        
        out[0] = x.__getitem__(cdata)

    def c_code(self, node, name, inputs, outputs, sub):
        view_ndim = node.outputs[0].ndim
        indices = inputs[1:]

        sio = StringIO.StringIO("""
        ssize_t %(name)s_starts[%(sz)s];
        ssize_t %(name)s_stops[%(sz)s];
        ssize_t %(name)s_steps[%(sz)s];
        """ % dict(name=name, sz=len(self.idx_list)))

        ndim = 0
        for i, idx in enumerate(self.idx_list):
            if isinstance(idx, Type):
                # Index by an input number
                print >>sio, """
                %(name)s_starts[%(i)s] = %(start)s;
                %(name)s_steps[%(i)s] = %(step)s;
                """ % dict(name=name, i=i, start=indices.pop(), step=0)
            elif isinstance(idx, slice):
                # index by a fixed slice
                print >>sio, """
                %(name)s_starts[%(i)s] = %(start)s;
                %(name)s_stops[%(i)s] = %(stop)s;
                %(name)s_steps[%(i)s] = %(step)s;
                """ % dict(i=i, name=name, start=idx.start, stop=idx.stop,
                           step=idx.step)
                ndim += 1
            else:
                # Index by a fixed number
                print >>sio, """
                %(name)s_starts[%(i)s] = %(start)s;
                %(name)s_steps[%(i)s] = %(step)s;
                """ % dict(name=name, i=i, start=idx, step=0)

        print >>sio, """
        if (%(out)s) {
            // Try to reuse the python object.
            GpuArray_clear(&%(out)s->ga);
        } else {
            %(out)s = new_GpuArray((PyObject *)&GpuArrayType, GpuArray_default_context);
        }
        if (!%(out)s) { %(fail)s }
        int %(name)s_err;
        %(name)s_err = GpuArray_index(&%(out)s->ga, &%(inp)s->ga,
                                      %(name)s_starts, %(name)s_steps,
                                      %(name)s_stops)
        if (%(name)s_err != GA_NO_ERROR) {
            Py_DECREF(%(out)s); %(out)s = NULL;
            PyErr_SetString(PyExc_RuntimeError, "Error during index");
            %(fail)s
        }
""" % dict(name=name, fail=sub['fail'], inp=inputs[0], out=outputs[0])

        return sio.getvalue()

    def c_code_cache_version(self):
        return (0,)

    def grad(self, inputs, grads):
        gz, = grads
        x = inputs[0]
        rest = inputs[1:]
        output = self(*inputs)
        if output.dtype.find('int') != -1:
            first = x.zeros_like(theano.config.floatX)
        else:
            first = GpuIncSubtensor(self.idx_list)(x.zeros_like(), gz, *rest)
        return ([first] + [DisconnectedType()()] * len(rest))
