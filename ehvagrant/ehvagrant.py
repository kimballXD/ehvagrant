#!/usr/bin/env python
"""Vagrant Manager.

Usage:
  cm-vagrant.py vagrant create --vms=<vmlist> [--box=BOX] [--template=TEMPLATE] [--output=OUTPUT] [--debug]
  cm-vagrant.py vagrant start [--vms=<vmList>] [--debug]
  cm-vagrant.py vagrant stop [--vms=<vmList>] [--debug]
  cm-vagrant.py vagrant suspend [--vms=<vmList>] [--debug]
  cm-vagrant.py vagrant destroy [--vms=<vmList>] [--debug]
  cm-vagrant.py vagrant info
  cm-vagrant.py vagrant ls
  cm-vagrant.py vagrant upload --from=FROM --to=TO [-r] [--vms=<vmlist>] [--debug]
  cm-vagrant.py vagrant download --from=FROM --to=TO [-r] [--vms=<vmlist>] [--debug]
  cm-vagrant.py vagrant ssh NAME [--debug]
  cm-vagrant.py vagrant run command COMMAND [--vms=<vmList>] [--debug]
  cm-vagrant.py vagrant run script SCRIPT [--data=PATH] [--vms=<vmList>] [--debug]


  cm-vagrant.py -h

Options:
  -h --help     Show this screen.
  --vm_list=<list_of_vms>  List of VMs separated by commas ex: node-1,node-2

Description:
   put a description here
   
Example:
   put an example here
"""
from __future__ import print_function
import fileinput
import re
import subprocess
import os
from docopt import docopt
from colorama import init
from termcolor import colored
import hostlist
import multiprocessing.dummy as mt
import queue
import time
import logging

# TODO: workspace should be in ~/.cloudmesh/vagrant
# TODO: if the workspace is not ther it needs to be created
# TODO: use captal letters as easier to document in other tools


class Vagrant(object):
    """
    TODO: doc
    """

    def __init__(self, debug=False):
        """
        TODO: doc

        :param debug:
        """
        self.workspace = "."
        self.experiment_path="./experiment"
#        self.workspace = "../configuration/"
#        self.experiment_path="../experiment"        
        self.ssh_config={}
        self.path = os.path.join(self.workspace, "Vagrantfile")
        self.debug = debug
        
        
    def _update_by_key(self, target, source, keys=None, key_dict=None):
        for x in keys:
            if source.get(x): # key exists and not none
                target.update({re.sub('^[-]+','',x):source[x]})
        for k,v in key_dict.items():
            if source.get(k):
                target.update({v:source[k]})                   
                
        return target

    def _nested_mkdir(self, path):
        parsed_path=path.split('/')
        for i in range(len(parsed_path)-1):
            d='/'.join(parsed_path[0:i+1])
            if not os.path.isdir(d):
                os.mkdir(d)

    def _get_host_names(self):
        """
        get all of the host names that exist in current vagrant environment
        """
        res=self.execute('vagrant status', result=True)
        if isinstance(res,Exception):
            print(res)
            return []
            
        res=res.decode('utf8')
        res=re.split('[\r\n]{1,2}',res)
        host_lines=res[res.index('',1)+1:res.index('',2)]
        host_names=[re.split('\s+',x)[0] for x in host_lines]
        return host_names

    def _scp(self, name, direction, source, dest, recursive):
        """
        upload file to / fetch file from the remote node using scp functionality available on local machine

        :param name: name of the node.
        :param direction: download or upload
        :param source: source file path 
        :param dest: destination file path 
        :return: None
        """         
        # get vagrant setting
        if name not in self.ssh_config:
            res=self.execute('vagrant ssh-config {}'.format(name), result=True)
            res=res.decode('utf8')            
            configs=[x.strip().split() for x in re.split('[\r\n]+',res) if x]
            configs=dict(zip([x[0] for x in configs], [x[1] for x in configs]))
            user=configs['User']
            key_file=os.path.normpath(configs['IdentityFile'])
            ip=configs['HostName']
            port=configs['Port']
                        
            #save to ssh_config
            self.ssh_config[name]={'user':user, 'ip':ip, 'port':port, 'key_file':key_file}
        else:
            user, ip, port, key_file=[self.ssh_config[name][x] for x in ['user','ip','port','key_file']]
            
        # submit 
        kwargs={'recursive': '-r' if recursive else '',
                'port':port,
                'key_file':key_file,
                'source':source,
                'user':user,
                'ip':ip,
                'dest':dest}     
                      
        if direction=='upload':
            logging.debug('upload {} to node {} with path {}...'.format(source, name, dest))
            template='scp {recursive} -P {port} -q -o LogLevel=QUIET -o StrictHostKeyChecking=no -i {key_file} {source} {user}@{ip}:{dest}'
            subprocess.call(template.format(**kwargs))
        elif direction=='download':
            logging.debug('download {} form the node {} with path {}...'.format(source, name, dest))
            template='scp {recursive} -P {port} -q -o LogLevel=QUIET -o StrictHostKeyChecking=no -i {key_file} {user}@{ip}:{source} {dest}'
            subprocess.call(template.format(**kwargs))
                     
    def _parse_run_result(self, res, template=None, report_kwargs=None):
        """
        parse running result, and (optionally) generating running report

        :param res: job result object
        :param template: template of running report
        :param report_kwargs: content dictionary of running result
        :return: str or dictionary:
        """         
        #parse run_report
        job_status='Finished' if not isinstance(res, Exception) else 'Failed'
        
        if job_status =='Finished':
            str_output=res.decode('utf8') if not isinstance(res, str) else res
            command_output=re.search('^\x04(.+?)\nreturn_code', str_output, re.MULTILINE|re.DOTALL)
            command_output=command_output.group(1).strip() if command_output else ""
            return_code=int(re.search('return_code: (\d+)', str_output).group(1))
            job_status=job_status if return_code!=0 else 'Success'
        else:                               
            command_output=res.stdout.decode('utf8')
            return_code='N.A.'
        
        ## return 
        parse_result={'job_status':job_status, 'return_code':return_code, 'output':command_output}
        if template and report_kwargs:
            report_kwargs.update(parse_result)
            return template.format(**report_kwargs)
        else:
            return parse_result

    def run_parallel(self, hosts, run_action, args, kwargs):                                            
        """
        run job in parallel fashion

        :param hosts: list of node names on which job runs
        :param run_action: running action function object
        :param args: positional arguments of running action function
        :param kwargs: keyword arguments of running action function
        :return: None:
        """      
        # initalize threading pool
        pool=mt.Pool(len(hosts))
        run_result=queue.Queue()
        
        # submit job to the threading pool and put the job result object into the result queue
        for name in hosts:
            cur_args=([name] + args)
            job=pool.apply_async(run_action, args=cur_args, kwds=kwargs)            
            run_result.put([name, job])
        pool.close()
        
        # retrieve the result          
        wait_time=5
        run_report=[]
        while run_result.qsize()>0:
            node, job_res=run_result.get()
            if not job_res.ready():
                run_result.put([node, job_res])
                logging.info('job assign to node {:<8s} is not finished yet! Wait for finishing.....'.format(node, wait_time))
                time.sleep(wait_time)
            else:
                run_report.append(job_res.get())
        
        #print report
        for x in run_report:print(x)
                
    def run_script(self, name, script_path, data=None, prefix_dest=False, report=True, report_alone=True):
        """
        run shell script on specified node, fetch the console output and data output if existed

        :param name: name of node
        :param script_path: local path of script file which will be executed on the node
        :param report: processing job running report. if False, return result object
        :param report_alone: print job running report. if False, return job running report
        :return: dictionary, subprocess.CalledProcessError
        """
        # building path
        script_name=os.path.basename(script_path)
        exp_folder_name='{}_{:.0f}'.format(script_name,time.time())
        guest_exp_folder_path='~/cm_experiment/{}'.format(exp_folder_name)
        guest_script_path='{}/{}'.format(guest_exp_folder_path,script_name)

        # ensure cm_experiment folder exists, in not, build cm_experiement folder
        cm_folder_query=self.run_command(name, 'ls -d ~/cm_experiment/', False)
        cm_folder_query=cm_folder_query['output']
        if 'No such file or directory' in cm_folder_query:
            self.run_command(name, 'mkdir ~/cm_experiment', False)
                           
        # build geust expreiment folder and ship sciript to it 
        self.run_command(name, 'mkdir {}'.format(guest_exp_folder_path), False)
        self.upload(name, source=script_path, dest=guest_script_path, recursive=False)
        
        # if there is some data must runing against, scp data to data folder
        if data:
            if os.path.isdir(data):
                self.upload(name, source=data, dest=guest_exp_folder_path, recursive=True)
                self.run_command(name, 'mv {base}/{data_folder} {base}/data'.format(base=guest_exp_folder_path, data_folder=re.split('[\\\\/]', data)[-1]))
            else:                
                self.run_command(name, 'mkdir {}/data/'.format(guest_exp_folder_path), False)
                self.upload(name, source=data, dest=guest_exp_folder_path)
                
        # run the script
        script_args=guest_exp_folder_path
        run_res=self.run_command(name, '. {} {} 2>&1 > {}/console_output.txt'.format(guest_script_path, script_args, guest_exp_folder_path), False)   
        
        # fetch console output
        if isinstance(run_res, subprocess.CalledProcessError):
            # TODO: if return error, how to modify the following process?
            pass
        elif isinstance(run_res, Exception):
            raise run_res
        else:
            console_output=self.run_command(name, 'cat {}/console_output.txt'.format(guest_exp_folder_path), False)
            run_res['output']=console_output['output']+'\n'+run_res['output']               
        
        # fetch output files if exists
        output_files_query=self.run_command(name, "ls {}/output/".format(guest_exp_folder_path), report=False)
        have_output_file=output_files_query['return_code']==0 and output_files_query['output'] # remote output folder exists and have files in it     
        
        if have_output_file:
            # build local experiment folder
            host_exp_folder_path='{}/{}/{}/'.format(self.experiment_path, name, exp_folder_name)  
            self._nested_mkdir(host_exp_folder_path)
            
            #fetch output files
            self.download(name, source="{}/output/".format(guest_exp_folder_path), dest=host_exp_folder_path, prefix_dest=prefix_dest, recursive=True)

        # processing the report
        if not report:
            return run_res
        
        else:
            template='\n'.join(['\n\n========= JOB REPORT =========',
                                'node_name: {name}',
                                'job_description: {job_type} "{command}"',
                                'job_status/node_return_code: {job_status} / {return_code}',
                                'remote job_folder: {remote_job_folder}',
                                'local output folder:{local_output_folder}',
                                'console output:\n{output}\n'])            
            
            report_kwargs={'name':name, 
                           'job_type':'run_script',
                           'remote_job_folder':guest_exp_folder_path+'/',
                           'local_output_folder': host_exp_folder_path+'/' if have_output_file else 'N.A.',
                           'command':script_path                          
                           }
            
            report_kwargs.update(run_res)            
            report=template.format(**report_kwargs)
            
            if report_alone:
                print(report)            
            else:
                return report            
            
    def run_command(self, name, command, data=None, report=True, report_alone=True):
        """
        run shell command in specified node

        :param name: name of node
        :param command: command executed on the node
        :param report: processing job running report. if False, return result object
        :param report_alone: print job running report. if False, return job running report
        :return: string, subprocess.CalledProcessError
        """
        #submit job
        logging.debug('exceute {} on node {}......'.format(command, name))        
        res=self.execute('vagrant ssh {} -c "echo -e \\"\x04\\";{}; echo \\"return_code: $?\\""'.format(name, command), result=True)                                       

        # processing result
        if not report:
            return self._parse_run_result(res) if not isinstance(res, Exception) else res
        
        else:
            template='\n'.join(['\n\n========= JOB REPORT =========',
                                'node_name: {name}',
                                'job_description: {job_type} "{command}"',
                                'console output:\n{output}\n'])
            report_kwargs={'name':name, 'job_type':'run_command', 'command':command}
            report=self._parse_run_result(res, template, report_kwargs)
            
            if report_alone:
                print(report)                
            else:
                return report                
                              
    def execute(self, command, result=False):
        """
        TODO: doc

        :param command:
        :return:
        """
        if self.debug:
            logging.debug(command.strip())
            logging.debug(self.workspace.strip())
                       
        if not result:
            subprocess.run(command.strip(),
                           cwd=self.workspace,
                           check=True,
                           shell=True)
        else:
            try:
                res=subprocess.check_output(command.strip(),
                                            cwd=self.workspace,
                                            shell=True, stderr=subprocess.STDOUT, input=b'\n')
                return res
            except Exception as e:
                return e

    def ssh(self, name):
        """
        TODO: doc

        :param name:
        :return:
        """
        self.execute("vagrant ssh " + str(name))
    
    
    def create(self, hosts, image='ubuntu/xenial64', output_path=None, template=None):
        """
        TODO: doc

        :return:                        
        """ 
        # prepare dict
        kwargs={}
        array=["'{}'".format(x) for x in hosts]       
        kwargs.update({'array':','.join(array)})
        kwargs.update({'image':image})
        
        # prepare template
        if not template:
            template="""
            Vagrant.configure("2") do |config|    
              ([{array}]).each do |name|
                config.vm.define "#{{name}}" do |node|
                  node.vm.box = "{image}"
                end
              end
            end
            """
        
        #write
        if not output_path:
            output_path=self.path
        with open(output_path, 'w') as out:
            out.write(template.format(**kwargs))        
        
    def start(self, name=None):
        """
        TODO: doc

        :param name:
        :return:
        """
        if name is None:
            # start all
            name = ""
        self.execute("vagrant up " + str(name))

    def stop(self, name=None):
        """
        TODO: doc

        :param name:
        :return:
        """
        if name is None:
            # start all
            name = ""
        self.execute("vagrant halt " + str(name))
        
    def suspend(self, name=None):
        """
        TODO: doc

        :param name:
        :return:
        """
        if name is None:
            # start all
            name = ""
        self.execute("vagrant suspend " + str(name))        

    def delete(self, name=None):
        """
        TODO: doc

        :param name:
        :return:
        """
        if name is None:
            name = ""
        self.execute("vagrant destroy " + str(name))

    def info(self, name=None):  # may rename to "info"
        """
        TODO: doc

        :param name:
        :return:
        """
        if name is None:
            # start all
            name = ""
        self.execute("vagrant status " + str(name))

    def list(self):  # may rename to "ls"
        """
        TODO: doc

        :return:
        """
        with open(self.path, 'r') as f:
            content = f.read()
        print (content)

    def download(self, name, source, dest, prefix_dest=False, recursive=False):
        """
        TODO: doc

        :return:
        """        
        if prefix_dest:
            path_split = re.split('[\\\\/]',dest)
            path_split.insert(-1, name)
            dest=os.path.join(*path_split)
            dest=re.sub('\\\\','/',dest)
                
        r=(not os.path.basename(source) or recursive)
        self._scp(name, 'download', source, dest, r)
    
    def upload(self, name, source, dest, recursive=False):
        """
        TODO: doc

        :return:
        """                        
        r=(not os.path.basename(source) or recursive)
        self._scp(name, 'upload', source, dest, r)

def process_arguments(arguments):
    """
    TODO: doc

    :param arguments:
    :return:
    """
    debug = arguments["--debug"]
    if debug:
        try:
            columns, rows = os.get_terminal_size(0)
        except OSError:
            columns, rows = os.get_terminal_size(1)

        print (colored(columns * '=', "red"))
        print (colored("Running in Debug Mode","red"))
        print (colored(columns * '=',"red"))
        print(arguments)
        print (colored(columns * '-',"red"))

        logging.basicConfig(level=logging.DEBUG)        
    else:
        logging.basicConfig(level=logging.INFO)


    if arguments.get("vagrant"):        
        
        provider = Vagrant(debug=debug)

        # parse argument
        hosts = []
        action = None
        kwargs = dict()
        args= []
        if arguments.get("create"):
            action = provider.create
            kwargs=provider._update_by_key(kwargs, arguments, ['--image','--template'], {'--output':'output_path'})
        elif arguments.get("start"):
            action = provider.start
        elif arguments.get("stop"):
            action = provider.stop
        elif arguments.get("suspend"):
            action = provider.suspend
        elif arguments.get("destroy"):
            action = provider.destroy
        elif arguments.get("info"):
            action = provider.status
        elif arguments.get("download"):
            action = provider.download
            args.append(arguments.get("FROM"))
            args.append(arguments.get("TO"))
            kwargs = provider._update_by_key(kwargs, arguments, key_dict={'-r':'recursive'})
        elif arguments.get("upload"):
            action = provider.upload
            args.append(arguments.get("FROM"))
            args.append(arguments.get("TO"))
            kwargs = provider._update_by_key(kwargs, arguments, key_dict={'-r':'recursive'})
        elif arguments.get("ssh"):
            action = provider.ssh
            args.append(arguments.get("NAME"))
        elif arguments.get("run") and arguments.get("command"):
            action = provider.run_command
            args.append(arguments.get("COMMAND"))
        elif arguments.get("run") and arguments.get("script"):
            action = provider.run_script
            args.append(arguments.get("SCRIPT"))
            kwargs = provider._update_by_key(kwargs, arguments,['--data'])
                        
        # do the action
        if action is not None:
            
            action_type = action.__name__   
            
            # aciton that has immediately execute       
            if action_type in ['ssh']:
                action(*args, **kwargs)
                return             
            
            # parse vms_hosts 
            if arguments.get("--vms"):
                vms_hosts = arguments.get("--vms")
                vms_hosts = hostlist.expand_hostlist(vms_hosts)
            else:
                vms_hosts=[]

            #action with vms_hosts                                   
            if action_type in ['create']:
                args.append(vms_hosts)
                action(*args, **kwargs)
                return                         
            elif action_type in ['start','stop','destroy','info','suspend'] and not vms_hosts:
                action()
                return
            
            # impute hosts
            if not vms_hosts:
                hosts = provider._get_host_names()
                if not hosts:
                    raise EnvironmentError('There is no host exists in the current vagrant project')
            else:
                hosts = vms_hosts                                            
    
            # action work with host                    
            if action_type in ['start','stop','destroy','info','suspend']:
                for node_name in hosts:
                    action(node_name)                
            else:
                # impute argument according to number of host
                if len(hosts)>1:
                    if action_type in ['run_command','run_script']:
                        kwargs.update({'report_alone':False})                             
                    if action_type in ['run_script, download']:
                        kwargs.update({'prefix_dest':True})                        
                        
                    provider.run_parallel(hosts, action, args, kwargs)                        
                    
                else:
                    if action_type in ['run_command','run_script']:
                        kwargs.update({'report_alone':True})                             
                    if action_type in ['run_script, download']:
                        kwargs.update({'prefix_dest':False})                        
                    
                    action(hosts[0], *args, **kwargs)                   
                   
#%%
def main():
    """
    TODO: doc

    :return:
    """
    arguments = docopt(__doc__, version='Vagrant Manager 1.0')
    process_arguments(arguments)


if __name__ == "__main__":
    main()
