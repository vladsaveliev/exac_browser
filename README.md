Usage
=======

*If you would like to use the ExAC browser, the most recent stable version is hosted at http://exac.broadinstitute.org*

Advanced: The following instructions are useful for cloning the browser (e.g. to load custom sites/coverage data).
Most users will not need to go through this process.

Installation
=======

### Getting the code

Create a directory to put all this stuff in. This will serve as the parent directory of the actual exac_browser repository 

    mkdir exac
    cd exac

First (as this can run in parallel), get the datasets that the browser uses and put them into an 'exac_data' directory:

    wget http://broadinstitute.org/~konradk/exac_browser/exac_browser.tar.gz .
    tar zxvf exac_browser.tar.gz
    cd ..

Now clone the repo: 

    git clone https://github.com/AstraZeneca-NGS/exac_browser.git

### Dependencies

Follow these instructions to get Python and Homebrew installed on your Mac:
http://docs.python-guide.org/en/latest/starting/install/osx/

Install MongoDB:

    brew install mongodb
    # or
    sudo port install mongodb

Create a directory to hold your mongo database files: 

    mkdir database

In a separate tab, start the mongo database server. Use NUMA runner that addresses 
mongo performance issues (see details here http://www.xtivia.com/running-mongodb-on-numa-hardware/):
    
    numactl --interleave=all mongod --dbpath database/

This local server needs to be running at all times when you are working on the site.
You could do this in the background if you want or set up some startup service,
but I think it's easier just to open a tab you can monitor.

Finally, you may want to keep the system in a virtualenv:

    sudo port install py27-virtualenv # Or whatever version

If so, you can create a python virtual environment where the browser will live:

    mkdir exac_env
    virtualenv exac_env
    source exac_env/bin/activate

### Installation

Install the python requirements:

    pip install -r requirements.txt

Note that this installs xBrowse too. Some packages will require Python headers (python-dev on some systems).

### Setup

At this point, it's probably worth quickly checking out the code structure if you haven't already :)

Now we must load the database from those flat files.
This is a single command, but it can take a while (can take advantage of parallel loads by modifying LOAD\_DB\_PARALLEL\_PROCESSES in exac.py):

    python manage.py load_db

You won't have to run this often - most changes won't require rebuilding the database.
That said, this is (and will remain) idempotent,
so you can run it again at any time if you think something might be wrong - it will reload the database from scratch.
You can also reload parts of the database using any of the following commands:

    python manage.py load_gene_models
    python manage.py load_population_coverage
    python manage.py load_variants_file
    python manage.py load_base_coverage
    python manage.py load_evaluate_capture_data
    # python manage.py load_cnv_models
    # python manage.py load_cnv_genes

Then run:

    python manage.py precalculate_metrics

Then, you need to create a cache for autocomplete and large gene purposes:

    python manage.py create_cache

### Running the site

Note that if you are revisiting the site after a break, make sure your virtualenv is `activate`'d.

You can run the development server with:

    python exac.py <HOST_IP>

And visit on your browser:
    
    <HOST_IP>:5000
    <HOST_IP>:5000/hg38/TS_UK_0031_SERD_EXOME
    <HOST_IP>:5000/hg38/TS_UK_0031_SERD_EXOME/031-03
    <HOST_IP>:5000/hg38/TS_UK_0031_SERD_EXOME/031-03/gene/EGFR


For testing, you can open up an interactive shell with:

    python manage.py shell

