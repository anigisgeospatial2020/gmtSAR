#!/usr/bin/env python3
# Alexey Pechnikov, Sep, 2021, https://github.com/mobigroup/gmtsar
from .SBAS_sbas import SBAS_sbas
from .tqdm_dask import tqdm_dask

class SBAS_geocode(SBAS_sbas):

    def intf_ra2ll(self, subswath=None, grids=None, debug=False):
        """
        Geocoding function based on interferogram geocode matrix to call from open_grids(geocode=True)
        """
        from tqdm.auto import tqdm
        import joblib
        import xarray as xr
        import numpy as np
        import os

        # that's possible to miss the first argument subswath
        assert subswath is not None or grids is not None, 'ERROR: define input grids'
        if grids is None:
            grids = subswath
            subswath = None

        # helper check
        if not 'y' in grids.dims and 'x' in grids.dims:
            print ('NOTE: the grid is not in radar coordinates, miss geocoding')
            return grids

        # check if subswath exists or return a single subswath for None
        subswath = self.get_subswath(subswath)

        intf_ra2ll_file = os.path.join(self.basedir, f'F{subswath}_intf_ra2ll.grd')
        intf_ll2ra_file = os.path.join(self.basedir, f'F{subswath}_intf_ll2ra.grd')

        matrix_ra2ll = xr.open_dataarray(intf_ra2ll_file, engine=self.engine, chunks=self.chunksize)
        matrix_ll2ra = xr.open_dataarray(intf_ll2ra_file, engine=self.engine, chunks=self.chunksize)

        # conversion works for a different 1st grid dimension size
        def ra2ll(grid):
            # input and transform grids should be the same
            grid = grid.reindex_like(matrix_ll2ra)
            # some interferograms have different y dimension and matrix has the largest
            # crop matrix y dimension when it is larger than current interferogram
            matrix_ra2ll_valid = xr.where(matrix_ra2ll<grid.size, matrix_ra2ll, -1)
            da_ll = xr.DataArray(np.where(matrix_ra2ll>=0, grid.values.reshape(-1)[matrix_ra2ll_valid], np.nan),
                coords=matrix_ra2ll_valid.coords)
            return da_ll

    #        def ra2ll(grid):
    #            return xr.DataArray(np.where(matrix_ra2ll>=0, grid.values.reshape(-1)[matrix_ra2ll], np.nan),
    #                coords=matrix_ra2ll.coords)

        # process single 2D raster
        if len(grids.dims) == 2:
            return ra2ll(grids)

        # process a set of 2D rasters
        with self.tqdm_joblib(tqdm(desc='Geocoding', total=len(grids))) as progress_bar:
            grids_ll = joblib.Parallel(n_jobs=-1)(joblib.delayed(ra2ll)(grids[item]) for item in range(len(grids)))
        grids_ll = xr.concat(grids_ll, dim=grids.dims[0])

        # add coordinates from original grids
        for coord in grids.coords:
            if coord in ['y', 'x']:
                continue
            grids_ll[coord] = grids[coord]

        return grids_ll

    def intf_ra2ll_matrix(self, subswath, intf_grids, debug=False):
        """
        Build interferogram geocoding matrix after interferogram processing required for open_grids(geocode=True)
        """
        from scipy.spatial import cKDTree
        import xarray as xr
        import numpy as np
        import os

        # use 2D grid grom the pairs stack
        # sometimes interferogram grids are different for one azimuth line so use the largest grid
        intf_grid = intf_grids.min('pair')

        trans_ra2ll_file = os.path.join(self.basedir, f'F{subswath}_trans_ra2ll.grd')
        intf_ra2ll_file  = os.path.join(self.basedir, f'F{subswath}_intf_ra2ll.grd')

        # trans.dat - file generated by llt_grid2rat (r a topo lon lat)"
        trans = self.get_trans_dat(subswath)
        lon_min, lon_max = trans[:,3].min(),trans[:,3].max()
        lat_min, lat_max = trans[:,4].min(),trans[:,4].max()

        # read translation table for the full DEM area
        trans_ra2ll = xr.open_dataarray(trans_ra2ll_file, engine=self.engine, chunks=self.chunksize)

        # build ra2ll translation matrix for interferogram coordinates and area only
        # each lan/lon cell has zero or one neighbour radar cell
        # each radar cell has one or multiple (on borders) neighbour lat/lon cells
        intf_ys, intf_xs = xr.broadcast(intf_grids.y, intf_grids.x)
        intf_yxs = np.stack([intf_ys.values.reshape(-1),intf_xs.values.reshape(-1)], axis=1)
        trans_yxs = np.stack([trans[:,1],trans[:,0]], axis=1)

        tree = cKDTree(intf_yxs, compact_nodes=False, balanced_tree=False)
        # use accurate distance limit as a half of the cell diagonal
        dy = intf_grids.y.diff('y')[0]
        dx = intf_grids.x.diff('x')[0]
        distance_limit = np.sqrt((dx/2.)**2 + (dy/2.)**2) + 1e-2
        d, inds = tree.query(trans_yxs, k = 1, distance_upper_bound=distance_limit, workers=8)

        # single integer index mask
        intf2trans = np.where(~np.isinf(d), inds, -1)
        # produce the same output array
        intf_ra2ll = xr.zeros_like(trans_ra2ll).rename('intf_ra2ll')
        intf_ra2ll.values = np.where(trans_ra2ll>=0, intf2trans[trans_ra2ll], -1)
        #assert intf_grid.size - 1 == intf_ra2ll.max(), 'ERROR: transform matrix and interferograms largest grid are different'
        assert intf_grid.size > intf_ra2ll.max(), \
            f'ERROR: transform matrix size {intf_grid.size} is too small for interferograms largest index {intf_ra2ll.max()}'
        # magic: add GMT attribute to prevent coordinates shift for 1/2 pixel
        intf_ra2ll.attrs['node_offset'] = 1
        # save to NetCDF file
        if os.path.exists(intf_ra2ll_file):
            os.remove(intf_ra2ll_file)
        intf_ra2ll.to_netcdf(intf_ra2ll_file, encoding={'intf_ra2ll': self.compression}, engine=self.engine)

#     def ra2ll(self, subswath, debug=False):
#         """
#         Create radar to geographic coordinate transformation matrix for DEM grid using geocoding table trans.dat
#         """
#         from scipy.spatial import cKDTree
#         import xarray as xr
#         import numpy as np
#         import os
# 
#         trans_ra2ll_file = os.path.join(self.basedir, f'F{subswath}_trans_ra2ll.grd')
# 
#         if os.path.exists(trans_ra2ll_file):
#             os.remove(trans_ra2ll_file)
# 
#         # trans.dat - file generated by llt_grid2rat (r a topo lon lat)"
#         trans = self.get_trans_dat(subswath)
#         lon_min, lon_max = trans[:,3].min(),trans[:,3].max()
#         lat_min, lat_max = trans[:,4].min(),trans[:,4].max()
# 
#         #dem = xr.open_dataset(in_dem_gridfile)
#         #dem = self.get_dem(geoloc=True)
#         dem = self.get_dem(geoloc=True).sel(lat=slice(lat_min, lat_max), lon=slice(lon_min, lon_max))
# 
#         trans_latlons = np.stack([trans[:,4],trans[:,3]], axis=1)
#         dem_lats, dem_lons = xr.broadcast(dem.lat,dem.lon)
#         dem_latlons = np.stack([dem_lats.values.reshape(-1),dem_lons.values.reshape(-1)], axis=1)
# 
#         tree = cKDTree(trans_latlons, compact_nodes=False, balanced_tree=False)
#         # use accurate distance limit as a half of the cell diagonal
#         dlat = dem.lat.diff('lat')[0]
#         dlon = dem.lon.diff('lon')[0]
#         distance_limit = np.sqrt((dlat/2.)**2 + (dlon/2.)**2) + 1e-6
#         d, inds = tree.query(dem_latlons, k = 1, distance_upper_bound=distance_limit, workers=8)
# 
#         # produce the same output array as dataset to be able to add global attributes
#         trans_ra2ll = xr.zeros_like(dem).rename('trans_ra2ll')
#         trans_ra2ll.values = np.where(~np.isinf(d), inds, -1).reshape(dem.shape)
#         # magic: add GMT attribute to prevent coordinates shift for 1/2 pixel
#         #trans_ra2ll.attrs['node_offset'] = 1
#         # save to NetCDF file
#         trans_ra2ll.to_netcdf(trans_ra2ll_file, encoding={'trans_ra2ll': self.compression}, engine=self.engine)

    def geocode_parallel(self, subswath=None, pairs=None, debug=False):
    
        assert pairs is not None or subswath is not None, 'ERROR: define pairs argument'
        if pairs is None and subswath is not None:
            pairs = subswath
            subswath = None
        
        subswath = self.get_subswath(subswath)
        if debug:
            print (f'DEBUG: build translation matrices for direct and inverse geocoding for subswath {subswath}')

        # build a new trans_dat for merged subswaths only
        if len(str(subswath)) > 1:
            self.topo_ra_parallel()

        # build DEM grid coordinates transform matrix
#        self.ra2ll(subswath, debug=debug)
    
        # transforms for interferogram grid
        grids = self.open_grids(pairs[:1], 'phasefilt')
        # build radar coordinates transformation matrix for the interferograms grid stack
        self.intf_ra2ll_matrix(subswath, grids, debug=debug)
        # build geographic coordinates transformation matrix for landmask and other grids
        self.intf_ll2ra_matrix(subswath, grids, debug=debug)

##########################################################################################
# TODO ll2ra
##########################################################################################

    def intf_ll2ra(self, subswath=None, grids=None):
        """
        Inverse geocoding function based on interferogram geocode matrix to call from open_grids(inverse_geocode=True)
        """
        # TODO: split to blocks and apply transform for the each block parallel
        # return intf grid where for the each pixel in the each block the input grid pixel value found
        # input grid reindex on trans_dat grid using nearest interp to fill possible gaps
        # 

    def intf_ll2ra_matrix(self, subswath, intf_grid, n_jobs=-1, interactive=False):
        from scipy.spatial import cKDTree
        import dask
        import xarray as xr
        import numpy as np
        import os

        # trans.dat - file generated by llt_grid2rat (r a topo lon lat)"
        trans_dat = self.get_trans_dat(subswath)
        trans_blocks_extents = self.get_trans_dat_blocks_extents(subswath, n_jobs=n_jobs)
        # TODO: create index grid
        #trans_idx = dask.array.arange(trans_dat.ll.size).reshape(trans_dat.ll.shape)
        #_, trans_idx = xr.unify_chunks(trans_dat.ll, trans_idx)
        trans_idx = dask.array.arange(trans_dat.ll.size, dtype=np.uint32)\
                    .reshape(trans_dat.ll.shape)*xr.ones_like(trans_dat.ll, dtype=np.uint32)
        #return trans_idx
        # define topo_ra grid
        #XMAX, yvalid, num_patch = self.PRM(subswath).get('num_rng_bins', 'num_valid_az', 'num_patches')
        #YMAX = yvalid * num_patch
        #print ('DEBUG: XMAX', XMAX, 'YMAX', YMAX)
        # use center pixel GMT registration mode
        #rngs = np.arange(1, XMAX+1, idec, dtype=np.int32)
        #azis = np.arange(1, YMAX+1, jdec, dtype=np.int32)
        # do not use coordinate names y,x, because the output grid saved as (y,y) in this case...
        azis = xr.DataArray(intf_grid.y, dims=['y'], coords={'y': intf_grid.y}).chunk(self.chunksize)
        rngs = xr.DataArray(intf_grid.x, dims=['x'], coords={'x': intf_grid.x}).chunk(self.chunksize)
        azis, rngs = xr.broadcast(azis, rngs)
        _, azis, rngs = xr.unify_chunks(intf_grid, azis, rngs)
        #print ('azis', azis)
        #print ('rngs', rngs)
        #azi, rng = [da.chunk(self.chunksize) for da in xr.broadcast(azis, rngs)]

        def calc_intf_ll2ra_matrix(azi, rng):
            # check thr arguments
            assert azi.shape == rng.shape, f'ERROR: {azi.shape} != {rng.shape}'

            # check the selected area bounds
            ymin, ymax, xmin, xmax = azi.min(), azi.max(), rng.min(), rng.max()
            #print ('ymin, ymax', ymin, ymax, 'xmin, xmax', xmin, xmax)
            # define corresponding trans_dat blocks
            ymask = (trans_blocks_extents[:,3]>=ymin-1)&(trans_blocks_extents[:,2]<=ymax+1)
            xmask = (trans_blocks_extents[:,5]>=xmin-1)&(trans_blocks_extents[:,4]<=xmax+1)
            blocks = trans_blocks_extents[ymask&xmask]
            print ('trans_dat blocks', blocks.astype(int))

            blocks_azis = []
            blocks_rngs = []
            blocks_idxs = []
            for iy, ix in blocks[:,:2].astype(int):
                #print ('iy, ix', iy, ix)
                block_azi = trans_dat.azi.data.blocks[iy, ix]
                block_rng = trans_dat.rng.data.blocks[iy, ix]
                block_idx = trans_idx.data.blocks[iy, ix]
                #print ('block_ele', block_ele.shape)

                blocks_azis.append(block_azi.reshape(-1))
                blocks_rngs.append(block_rng.reshape(-1))
                blocks_idxs.append(block_idx.reshape(-1))
            blocks_azis = np.concatenate(blocks_azis)
            blocks_rngs = np.concatenate(blocks_rngs)
            blocks_idxs = np.concatenate(blocks_idxs)

            # build index tree - dask arrays computes automatically
            source_yxs = np.stack([blocks_azis, blocks_rngs], axis=1)
            tree = cKDTree(source_yxs, compact_nodes=False, balanced_tree=False)

            # query the index tree
            target_yxs = np.stack([azi.reshape(-1), rng.reshape(-1)], axis=1)
            d, inds = tree.query(target_yxs, k = 1, workers=1)
            # compute dask array to prevent ineffective index loockup on it
            matrix = blocks_idxs.compute()[inds].reshape(azi.shape)
            return matrix

        # xarray wrapper
        matrix_ra = xr.apply_ufunc(
            calc_intf_ll2ra_matrix,
            azis,
            rngs,
            dask='parallelized',
            vectorize=False,
            output_dtypes=[np.uint32],
        ).rename('intf_ll2ra')

        if interactive:
            # do not flip vertically because it's returned as is without SBAS.get_topo_ra() function
            return matrix_ra

        # save to NetCDF file
        filename = self.get_filenames(subswath, None, 'intf_ll2ra')
        #print ('filename', filename)
        # to resolve NetCDF rewriting error
        if os.path.exists(filename):
            os.remove(filename)
        # flip vertically for GMTSAR compatibility reasons
        handler = matrix_ra.to_netcdf(filename,
                                    encoding={'intf_ll2ra': self.compression},
                                    engine=self.engine,
                                    compute=False)
        return handler

    def intf_ll2ra_matrix_parallel(self, pairs, interactive=False):
        import numpy as np
        import xarray as xr
        import dask

        # use 2D grid grom the pairs stack
        # sometimes interferogram grids are different for one azimuth line so use the largest grid
        intf_grids = self.open_grids(pairs, 'phasefilt')
        intf_grid = intf_grids.min('pair')
        delayed = self.intf_ll2ra_matrix(None, intf_grid, interactive=interactive)

        if not interactive:
            tqdm_dask(dask.persist(delayed), desc='Interferogram ll2ra Transform Computing')
        else:
            return delayed

    # y,x grid
    def get_intf_ll2ra(self):
        return self.open_grids(None, 'intf_ll2ra')

    # TODO: split to blocks and apply transform for the each block parallel
    def intf_ll2ra(self, grids=None):
        """
        Inverse geocoding function based on interferogram geocode matrix to call from open_grids(inverse_geocode=True)
        """
        import xarray as xr
        import numpy as np

        # helper check
        if not 'lat' in grids.dims and 'lon' in grid.dims:
            print ('NOTE: the grid is not in geograpphic coordinates, miss geocoding')
            return grids

        # unify the input grids for transform matrix defined on the trans_dat grid (lat, lon)
        trans_dat = self.get_trans_dat()
        grids = grids.interp_like(trans_dat.ll, method='nearest')

        matrix_ll2ra = self.get_intf_ll2ra()

        def ll2ra(grid):
            # transform input grid to the trans_ra2ll where the geocoding matrix defined
            # only nearest interpolation allowed to save values of binary masks
            return xr.DataArray(np.where(matrix_ll2ra != np.uint32(-1),
                                         grid.reshape(-1)[matrix_ll2ra],
                                         np.nan),
                                coords=matrix_ll2ra.coords).expand_dims('new').values

        # xarray wrapper
        grids_ra = xr.apply_ufunc(
            ll2ra,
            (grids.expand_dims('new') if len(grids.dims)==2 else grids).chunk({'lat':-1, 'lon':-1}),
            input_core_dims=[['lat','lon']],
            exclude_dims=set(('lat','lon')),
            dask='parallelized',
            vectorize=False,
            output_dtypes=[np.float32],
            output_core_dims=[['y','x']],
            dask_gufunc_kwargs={'output_sizes': {'y': matrix_ll2ra.shape[0], 'x': matrix_ll2ra.shape[1]}},
        ).chunk(self.chunksize)

        return (grids_ra[0] if len(grids.dims)==2 else grids_ra)
