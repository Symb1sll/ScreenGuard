"""A stuck native inference cannot hold a camera worker or shutdown indefinitely."""
import multiprocessing as mp
import time
from app.services.models import get_model

def inference_worker(model_id,connection):
    try:
        if isinstance(model_id,dict):
            from screen_guard_core import ModelPackage
            model=ModelPackage(model_id['package_path'])
        else:model=get_model(model_id)
        connection.send(('ready',model.id,model.validated,model.meta))
        while True:
            frame=connection.recv()
            if frame is None:return
            try:connection.send(('scores',model.predict(frame)))
            except Exception as exc:connection.send(('error',type(exc).__name__))
    except (EOFError,BrokenPipeError):pass
    except Exception as exc:
        try:connection.send(('error',type(exc).__name__))
        except (EOFError,BrokenPipeError,OSError):pass
    finally:connection.close()

class TimedModel:
    def __init__(self,model_id,stop,load_timeout=30,predict_timeout=10):
        self.stop=stop;self.predict_timeout=predict_timeout;self.broken=False
        ctx=mp.get_context('spawn');self.connection,child=ctx.Pipe()
        self.process=ctx.Process(target=inference_worker,args=(model_id,child),daemon=True)
        self.process.start();child.close()
        try:
            result=self.receive(load_timeout)
            if result[0]!='ready':raise ValueError('模型初始化失败，请检查模型包')
            _,self.id,self.validated,self.meta=result
        except BaseException:self.close();raise
    def receive(self,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.stop.is_set():raise InterruptedError('采集已停止')
            if self.connection.poll(.1):
                try:return self.connection.recv()
                except EOFError:raise RuntimeError('推理进程退出') from None
            if not self.process.is_alive():raise RuntimeError('推理进程退出')
        raise TimeoutError('推理超时')
    def predict(self,frame):
        if self.broken:raise RuntimeError('推理进程需重启')
        try:
            self.connection.send(frame);result=self.receive(self.predict_timeout)
            if result[0]!='scores':raise RuntimeError('模型推理失败')
            return result[1]
        except BaseException:
            self.broken=True;self.close();raise
    def close(self):
        if self.process.is_alive():self.process.terminate()
        self.process.join(timeout=1);self.connection.close()
